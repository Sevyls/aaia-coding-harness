"""Run one command with a timeout, bounded output and no leftover child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from coding_harness.domain import CommandResult
from coding_harness.output import MARKER, shorten


def run_bounded(
    argv: Sequence[str],
    *,
    timeout: float,
    output_limit: int,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    on_timeout: Callable[[], None] | None = None,
) -> CommandResult:
    """Run ``argv`` without a shell in its own process group.

    Output goes to a temporary file instead of a pipe, so a chatty command cannot block
    and memory stays bounded. On timeout or interrupt the whole process group is killed.
    """
    started = time.monotonic()
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                **_NEW_GROUP,
            )
        except OSError as exc:
            return CommandResult(
                command=list(argv),
                exit_code=None,
                output="",
                error=f"could not start {argv[0]!r}: {exc.strerror or exc}",
            )

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Also runs on KeyboardInterrupt: never leave the process group behind.
            _kill_group(proc)
        if timed_out and on_timeout is not None:
            on_timeout()

        output, truncated = _read_bounded(sink, output_limit)

    return CommandResult(
        command=list(argv),
        exit_code=proc.returncode,
        output=output,
        truncated=truncated,
        timed_out=timed_out,
        duration=time.monotonic() - started,
    )


if os.name == "nt":
    _NEW_GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    _NEW_GROUP = {"start_new_session": True}


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        # Windows has no process groups to signal: kill the process tree instead.
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        proc.wait()
        return
    # start_new_session=True makes the child a group leader, so its pid is the group id.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # the group is already gone
    proc.wait()


def _read_bounded(sink: BinaryIO, limit: int) -> tuple[str, bool]:
    size = sink.seek(0, os.SEEK_END)
    sink.seek(0)
    budget = limit * 4  # enough bytes for `limit` characters of UTF-8
    if size <= budget:
        return shorten(sink.read().decode("utf-8", "replace"), limit)

    head = sink.read(budget // 2).decode("utf-8", "replace")[: limit // 2]
    sink.seek(size - budget // 2)
    tail = sink.read().decode("utf-8", "replace")[-(limit - limit // 2):]
    marker = MARKER.format(omitted=f"about {size - budget} bytes")
    return f"{head}\n{marker}\n{tail}", True
