"""Disposable copy of the target repository, plus diff and changed files."""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The repository is untrusted: never run its hooks or an fsmonitor command.
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
       # Keep files byte-for-byte as committed, even where Git for Windows defaults to CRLF.
       "-c", "core.autocrlf=false"]


class WorkspaceError(Exception):
    pass


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            [*GIT, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False,
        )  # fmt: skip
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

    @classmethod
    def open(cls, run_dir: Path, commit: str) -> Workspace:
        """Reopen a saved copy. Its HEAD must still be the starting commit."""
        repo = run_dir / "repo"
        if not (repo / ".git").exists():
            raise WorkspaceError(f"the session's working copy is missing: {repo}")
        head = _git(["rev-parse", "HEAD"], cwd=repo).strip()
        if head != commit:
            raise WorkspaceError(f"HEAD of the working copy is {head}, not the starting commit {commit}")
        return cls(run_dir=run_dir, repo=repo, commit=head)

    def fingerprint(self) -> str:
        """Hash of HEAD plus every change in the working copy, including new files.

        Equal fingerprints mean the copy is exactly as it was, the harness's own edits included.
        """
        head = _git(["rev-parse", "HEAD"], cwd=self.repo).strip()
        return hashlib.sha256(f"{head}\0{self.diff()}".encode("utf-8")).hexdigest()

    def changed_files(self) -> list[tuple[str, str]]:
        """(status, path) pairs, e.g. ("M", "src/app.py") or ("A", "src/new.py")."""
        self._include_new_files()
        out = _git(["diff", "--name-status", "--no-renames"], cwd=self.repo)
        return [(status, path) for status, path in (line.split("\t", 1) for line in out.splitlines())]

    def diff(self) -> str:
        self._include_new_files()
        return _git(["diff", "--no-color", "--no-ext-diff", "--no-textconv"], cwd=self.repo)

    def original(self, path: str) -> str | None:
        """The file's content at the starting commit, or None if it did not exist there."""
        try:
            return _git(["show", f"{self.commit}:{path}"], cwd=self.repo)
        except WorkspaceError:
            return None

    def _include_new_files(self) -> None:
        # Intent-to-add makes untracked files show up in `git diff` without staging content.
        _git(["add", "--intent-to-add", "--all"], cwd=self.repo)
