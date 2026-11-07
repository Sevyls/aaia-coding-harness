"""Saved sessions: the frozen task, a checkpoint after every step, and the write-ahead event log.

A session is one run directory (``<workspace_dir>/<id>/``):

- ``task.json``: the task, project, exact starting commit, checks and limits as they were at the start
- ``checkpoint.json``: replaced atomically after every model reply and every tool result
- ``events.jsonl``: every event, appended and flushed as it happens. A ``tool_call`` is written
  before the tool runs and its ``tool_result`` after, so a call without a result is an action that
  was interrupted: it may or may not have taken effect.
- ``repo/`` (the working copy), ``trace.json`` (summary at the end), ``pid`` (while running)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from coding_harness.domain import Event, RunStats

SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SessionError(Exception):
    """The session does not exist or cannot be used for the requested action."""


class SessionStatus(StrEnum):
    RUNNING = "running"
    CANCELLED = "cancelled"  # stopped by the user; can be resumed
    PAUSED = "paused"  # a resume found a mismatch; nothing runs until the user decides
    FINISHED = "finished"  # the agent stopped and the final checks ran


@dataclass
class Checkpoint:
    status: SessionStatus
    step: int  # the last model request that was made
    stats: RunStats
    messages: list[dict[str, Any]]
    seen: list[list[Any]]  # loop detection: [tool, arguments, repository state, count]
    fingerprint: str  # working copy (HEAD + diff) when the checkpoint was written
    stop_reason: str | None = None
    note: str = ""  # why the session is paused or cancelled
    saved_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        data = dict(data, status=SessionStatus(data["status"]), stats=RunStats(**data["stats"]))
        return cls(**data)


def seen_to_list(seen: Counter) -> list[list[Any]]:
    return [[*key, count] for key, count in seen.items()]


def seen_from_list(items: list[list[Any]]) -> Counter:
    return Counter({tuple(item[:-1]): item[-1] for item in items})


class Session:
    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        self.id = run_dir.name

    @property
    def repo(self) -> Path:
        return self.dir / "repo"

    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"

    @property
    def trace_path(self) -> Path:
        return self.dir / "trace.json"

    def save_task(self, record: dict[str, Any]) -> None:
        _write_json(self.dir / "task.json", record)

    def task(self) -> dict[str, Any]:
        return _read_json(self.dir / "task.json")

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        _write_json(self.dir / "checkpoint.json", asdict(checkpoint))

    def checkpoint(self) -> Checkpoint | None:
        path = self.dir / "checkpoint.json"
        return Checkpoint.from_dict(_read_json(path)) if path.exists() else None

    def events(self) -> list[Event]:
        if not self.events_path.exists():
            return []
        events = []
        with self.events_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    events.append(Event(**json.loads(line)))
                except (ValueError, TypeError):
                    pass  # a line cut off by a crash; everything before it is intact
        return events

    def uncertain_actions(self) -> list[dict[str, Any]]:
        """Tool calls that were started but never returned a result, and were not dealt with yet."""
        calls, closed = {}, set()
        for event in self.events():
            op = event.data.get("op")
            if op is None:
                continue
            if event.kind == "tool_call":
                calls[op] = event.data
            elif event.kind in ("tool_result", "interrupted_action"):
                closed.add(op)
        return [data for op, data in calls.items() if op not in closed]

    # The pid of the process that runs the session, so `cancel` can stop it from another terminal.

    def mark_running(self) -> None:
        (self.dir / "pid").write_text(str(os.getpid()), encoding="utf-8")

    def mark_stopped(self) -> None:
        (self.dir / "pid").unlink(missing_ok=True)

    def running_pid(self) -> int | None:
        """The pid of a live harness process running this session, else None (stale pid files too)."""
        try:
            pid = int((self.dir / "pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                                     text=True, timeout=10, check=False).stdout  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return None
        # A recycled pid belongs to some other program: never signal that.
        return pid if "coding-harness" in command or "coding_harness" in command else None


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def open(self, session_id: str) -> Session:
        if not SESSION_ID.match(session_id) or not (self.base_dir / session_id / "task.json").is_file():
            raise SessionError(f"no such session: {session_id!r} (list them with `coding-harness sessions`)")
        return Session(self.base_dir / session_id)

    def list(self) -> list[Session]:
        if not self.base_dir.is_dir():
            return []
        return [Session(d) for d in sorted(self.base_dir.iterdir()) if (d / "task.json").is_file()]


def _write_json(path: Path, data: Any) -> None:
    """Write to a temporary file, then rename: a crash leaves the old file or the new one, never half."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                                     prefix=f".{path.name}.") as handle:  # fmt: skip
        json.dump(data, handle, indent=2, default=str)
    os.replace(handle.name, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionError(f"cannot read {path}: {exc}") from None
