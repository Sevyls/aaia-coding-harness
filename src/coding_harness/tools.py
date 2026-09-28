"""Repository tools: list, read, search and edit files inside the allowed repository copy."""

from __future__ import annotations

import ast
import os
import re
import warnings
from pathlib import Path

SKIPPED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SHOWN_LINE_CHARS = 300


class ToolError(Exception):
    """The action ran but failed. The message goes back to the model."""


class PermissionDenied(ToolError):
    """The action is not allowed. Nothing was read or written."""


class RepositoryTools:
    def __init__(
        self,
        root: Path,
        *,
        max_write_chars: int = 100_000,
        max_list_entries: int = 400,
        max_search_matches: int = 100,
    ) -> None:
        self.root = root.resolve()
        self.max_write_chars = max_write_chars
        self.max_list_entries = max_list_entries
        self.max_search_matches = max_search_matches

    def resolve(self, path: str) -> Path:
        """Map a model-supplied path to a real path inside the repository, or refuse.

        Symlinks are resolved before the check, so a link pointing outside is refused too.
        """
        if "\0" in path:
            raise PermissionDenied("path must not contain NUL characters")
        path = path or "."  # models often send "" for the repository root
        if Path(path).is_absolute():
            raise PermissionDenied(
                f"absolute paths are not allowed: {path!r}; use a path relative to the repository root"
            )
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionDenied(f"path is outside the repository: {path!r}")
        relative = resolved.relative_to(self.root)
        if relative.parts and relative.parts[0] == ".git":
            raise PermissionDenied("the .git directory is off limits")
        return resolved

    def display(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    def list_files(self, path: str = ".") -> str:
        start = self.resolve(path)
        if not start.is_dir():
            raise ToolError(f"not a directory: {path!r}")
        files = list(self._walk(start))
        shown = files[: self.max_list_entries]
        lines = [self.display(f) for f in shown]
        if len(files) > len(shown):
            lines.append(f"... {len(files) - len(shown)} more files not shown; list a subdirectory")
        return "\n".join(lines) if lines else "(no files)"

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self.resolve(path)
        text = self._read_text(target, path)
        lines = text.splitlines(keepends=True)
        end = len(lines) if end_line is None else min(end_line, len(lines))
        if start_line > max(end, 1):
            raise ToolError(f"start_line {start_line} is past the end of {path!r} ({len(lines)} lines)")
        body = "".join(lines[start_line - 1 : end])
        return f"{self.display(target)} (lines {start_line}-{end} of {len(lines)})\n{body}"

    def search(self, pattern: str, path: str = ".", regex: bool = False) -> str:
        start = self.resolve(path)
        try:
            compiled = re.compile(pattern if regex else re.escape(pattern))
        except re.error as exc:
            raise ToolError(f"invalid regular expression {pattern!r}: {exc}") from None
        files = [start] if start.is_file() else self._walk(start)
        matches: list[str] = []
        for file in files:
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable files are skipped
            for number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    matches.append(f"{self.display(file)}:{number}: {line[:MAX_SHOWN_LINE_CHARS]}")
                    if len(matches) >= self.max_search_matches:
                        matches.append(f"... stopped after {self.max_search_matches} matches")
                        return "\n".join(matches)
        return "\n".join(matches) if matches else f"no matches for {pattern!r}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        target = self.resolve(path)
        text = self._read_text(target, path)
        count = text.count(old_text)
        if count == 0:
            raise ToolError(
                f"old_text was not found in {path!r}; read the file and copy the text exactly, "
                "including indentation"
            )
        if count > 1:
            raise ToolError(
                f"old_text occurs {count} times in {path!r}; include more surrounding lines "
                "so it is unique"
            )
        self._write_text(target, text.replace(old_text, new_text, 1), previous=text)
        return (
            f"edited {self.display(target)}: replaced {old_text.count(chr(10)) + 1} line(s) "
            f"with {new_text.count(chr(10)) + 1} line(s)"
        )

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        if target.is_dir():
            raise ToolError(f"{path!r} is a directory")
        existed = target.is_file()
        previous = self._read_text(target, path) if existed else None
        self._write_text(target, content, previous=previous)
        action = "overwrote" if existed else "created"
        return f"{action} {self.display(target)} ({len(content)} characters)"

    def _walk(self, start: Path):
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    def _read_text(self, target: Path, path: str) -> str:
        if not target.is_file():
            raise ToolError(f"file not found: {path!r}")
        try:
            with target.open(encoding="utf-8", newline="") as handle:
                return handle.read()
        except UnicodeDecodeError:
            raise ToolError(f"{path!r} is not a UTF-8 text file") from None

    def _write_text(self, target: Path, content: str, previous: str | None) -> None:
        """Write after the guardrails pass; a rejected write leaves the file system unchanged."""
        if len(content) > self.max_write_chars:
            raise PermissionDenied(
                f"write of {len(content)} characters exceeds the limit of {self.max_write_chars}"
            )
        if target.suffix == ".py":
            self._check_syntax(target, content, previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)

    def _check_syntax(self, target: Path, content: str, previous: str | None) -> None:
        """Reject a change that turns valid Python into invalid Python (SWE-agent's edit guardrail).

        Only parses the code, never runs it. A file that was already broken may still be edited.
        """
        error = _syntax_error(content)
        if error is None or (previous is not None and _syntax_error(previous) is not None):
            return
        lines = content.splitlines()
        line = lines[error.lineno - 1].strip() if error.lineno and error.lineno <= len(lines) else ""
        raise ToolError(
            f"rejected: the change would introduce a syntax error in {self.display(target)} "
            f"(line {error.lineno}: {error.msg}: {line!r}); the file was not changed. "
            "Check indentation and brackets, then try again."
        )


def _syntax_error(source: str) -> SyntaxError | None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # e.g. invalid escape sequences are not our concern here
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return exc
        except ValueError as exc:  # e.g. NUL bytes
            return SyntaxError(str(exc))
    return None
