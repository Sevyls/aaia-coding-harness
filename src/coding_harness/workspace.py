"""Disposable copy of the target repository, plus diff and changed files."""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The repository is untrusted: never run its hooks or an fsmonitor command.
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


class WorkspaceError(Exception):
    pass


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            [*GIT, *args], cwd=cwd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"git {' '.join(args)} could not run: {exc}") from None
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


@dataclass(frozen=True)
class Workspace:
    run_dir: Path
    repo: Path
    commit: str

    @classmethod
    def create(
        cls, source: Path, commit: str, base_dir: Path, run_id: str | None = None
    ) -> Workspace:
        """Clone ``source`` at ``commit`` into a fresh directory without a remote."""
        run_id = run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        run_dir = base_dir / run_id
        repo = run_dir / "repo"
        run_dir.mkdir(parents=True)
        _git(["clone", "--quiet", "--no-hardlinks", "--no-checkout", str(source), str(repo)])
        _git(["checkout", "--quiet", "--detach", commit], cwd=repo)
        _git(["remote", "remove", "origin"], cwd=repo)  # nothing to push to
        head = _git(["rev-parse", "HEAD"], cwd=repo).strip()
        return cls(run_dir=run_dir, repo=repo, commit=head)

    def changed_files(self) -> list[tuple[str, str]]:
        """(status, path) pairs, e.g. ("M", "src/app.py") or ("A", "src/new.py")."""
        self._include_new_files()
        out = _git(["diff", "--name-status", "--no-renames"], cwd=self.repo)
        return [(status, path) for status, path in (line.split("\t", 1) for line in out.splitlines())]

    def diff(self) -> str:
        self._include_new_files()
        return _git(["diff", "--no-color", "--no-ext-diff", "--no-textconv"], cwd=self.repo)

    def _include_new_files(self) -> None:
        # Intent-to-add makes untracked files show up in `git diff` without staging content.
        _git(["add", "--intent-to-add", "--all"], cwd=self.repo)
