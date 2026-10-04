"""Repository tools: list, read, search and edit files inside the allowed repository copy."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import warnings
from pathlib import Path

from pyflakes import checker as pyflakes_checker
from pyflakes import messages as pyflakes_messages

from coding_harness import lint

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
        # Each written file's content at the start of the run (None: it did not exist), so lint
        # feedback compares with the start, not just with the previous edit.
        self._originals: dict[Path, str | None] = {}
        # Content hash of every file written so far. The same request in the same repository
        # state gets the same result; the controller uses state() to spot loops.
        self._written: dict[str, str] = {}

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
        """Replace ``old_text`` once. Small models often get indentation wrong, so as a fallback
        ``old_text`` may match ignoring indentation, and ``new_text`` is re-indented when that is
        the only way to keep the file valid Python. A correct edit is applied exactly as sent."""
        target = self.resolve(path)
        text = self._read_text(target, path)
        count = text.count(old_text)
        if count > 1:
            raise _not_unique(path, count)
        note = ""
        if count == 1:
            start, end = text.index(old_text), text.index(old_text) + len(old_text)
            content = text[:start] + new_text + text[end:]
            # old_text began after the line's indentation, so the later lines of new_text
            # need that indentation too; the model often leaves it out.
            indent = text[text.rfind("\n", 0, start) + 1 : start]
            if indent and not indent.strip() and _syntax_error(content) is not None:
                fixed = text[:start] + _reindent(new_text, "", indent, skip_first=True) + text[end:]
                if _syntax_error(fixed) is None:
                    content, note = fixed, " (indented the lines of new_text to match the file)"
        else:
            start, end, matches, model_indent, file_indent = _match_ignoring_indentation(text, old_text)
            if matches == 0:
                raise ToolError(
                    f"old_text was not found in {path!r}; read the file and copy the text exactly, "
                    "including indentation"
                )
            if matches > 1:
                raise _not_unique(path, matches)
            if old_text.endswith("\n") and new_text.endswith("\n"):
                new_text = new_text[:-1]  # the matched block excludes its last line ending
            content = text[:start] + _reindent(new_text, model_indent, file_indent) + text[end:]
            note = " (old_text matched with different indentation; re-indented new_text to match)"
        if content == text:
            raise ToolError(
                f"nothing would change in {path!r}: new_text is the same as old_text; "
                "put the changed code in new_text"
            )
        self._write_text(target, content, previous=text)
        return (
            f"edited {self.display(target)}: replaced {old_text.count(chr(10)) + 1} line(s) "
            f"with {new_text.count(chr(10)) + 1} line(s){note}" + self._lint_note(target, text, content)
        )

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        if target.is_dir():
            raise ToolError(f"{path!r} is a directory")
        existed = target.is_file()
        previous = self._read_text(target, path) if existed else None
        self._write_text(target, content, previous=previous)
        action = "overwrote" if existed else "created"
        return f"{action} {self.display(target)} ({len(content)} characters)" + self._lint_note(
            target, previous, content
        )

    def state(self) -> str:
        """Fingerprint of the repository content. Only the harness tools write to the copy, so
        the hashes of the written files are enough. An edit that is undone again returns to an
        earlier fingerprint, so edit/undo cycles are recognized as loops too."""
        return hashlib.sha256(repr(sorted(self._written.items())).encode()).hexdigest()

    def lint(self) -> str:
        """Lint problems introduced so far in the Python files this run has written."""
        found, checked = [], 0
        for target, original in self._originals.items():
            if target.suffix != ".py" or not target.is_file():
                continue
            checked += 1
            current = self._read_text(target, self.display(target))
            found += [f"{self.display(target)}:{p}" for p in lint.new_problems(original, current)]
        if not found:
            return f"lint: no new problems in {checked} changed Python file(s)"
        return "lint: problems introduced by this run (fix them before finish):\n" + "\n".join(found)

    def _lint_note(self, target: Path, previous: str | None, content: str) -> str:
        """Lint feedback after a write. The write is kept; the final lint check would fail."""
        if target.suffix != ".py":
            return ""
        original = self._originals.get(target, previous)
        now = lint.new_problems(original, content)
        if not now:
            had = previous is not None and lint.new_problems(original, previous)
            return "\nlint: the problems this run introduced in the file are fixed" if had else ""
        listed = "\n".join(f"  {problem}" for problem in now[:10])
        more = f"\n  ... and {len(now) - 10} more" if len(now) > 10 else ""
        return (f"\nwarning: compared with the start of the run, the file has {len(now)} new lint "
                f"problem(s); the final lint check fails until they are fixed:\n{listed}{more}")  # fmt: skip

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
            self._check_names(target, content, previous)
        self._originals.setdefault(target, previous)
        if content == self._originals[target]:
            self._written.pop(self.display(target), None)  # back to the start of the run
        else:
            self._written[self.display(target)] = hashlib.sha256(content.encode("utf-8")).hexdigest()
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
        raise ToolError(
            f"rejected: the change would introduce a syntax error in {self.display(target)} "
            f"(line {error.lineno}: {error.msg}); the file was not changed. "
            "Check indentation and brackets, then try again. The changed file would look like this:\n"
            + _numbered(content, error.lineno or 1)
        )

    def _check_names(self, target: Path, content: str, previous: str | None) -> None:
        """Reject a change that uses names defined nowhere (SWE-agent runs flake8 F821 for this).

        Uses pyflakes, which only analyses the code. Names that were already undefined before
        the change are not counted, so the check never blocks unrelated edits.
        """
        if previous is not None and _syntax_error(previous) is not None:
            return
        before = _undefined_names(previous) if previous is not None else {}
        added = {name: line for name, line in _undefined_names(content).items() if name not in before}
        if not added:
            return
        names = ", ".join(f"{name} (line {line})" for name, line in added.items())
        raise ToolError(
            f"rejected: the change uses names that are not defined in {self.display(target)}: "
            f"{names}; the file was not changed. Define or import them first, then use them. "
            "The changed file would look like this:\n" + _numbered(content, min(added.values()))
        )


def _not_unique(path: str, count: int) -> ToolError:
    return ToolError(
        f"old_text occurs {count} times in {path!r}; include more surrounding lines so it is unique"
    )


def _match_ignoring_indentation(text: str, old_text: str) -> tuple[int, int, int, str, str]:
    """Find whole lines equal to ``old_text`` up to leading and trailing whitespace.

    Returns the span of the last match without its final line ending, the number of matches,
    and the indentation of the first non-blank line in ``old_text`` and in the file.
    """
    wanted = [line.strip() for line in old_text.splitlines()]
    if not any(wanted):
        return 0, 0, 0, "", ""
    lines = text.splitlines(keepends=True)
    stripped = [line.strip() for line in lines]
    first = next(i for i, line in enumerate(wanted) if line)
    model_indent = _leading(old_text.splitlines()[first])
    span, count, file_indent = (0, 0), 0, ""
    for i in range(len(lines) - len(wanted) + 1):
        if stripped[i : i + len(wanted)] == wanted:
            start = sum(len(line) for line in lines[:i])
            last = lines[i + len(wanted) - 1]
            end = start + sum(len(line) for line in lines[i : i + len(wanted)])
            end -= len(last) - len(last.rstrip("\r\n"))
            span, count, file_indent = (start, end), count + 1, _leading(lines[i + first])
    return span[0], span[1], count, model_indent, file_indent


def _reindent(text: str, old: str, new: str, *, skip_first: bool = False) -> str:
    """Replace the indentation ``old`` with ``new`` on every non-blank line that starts with it."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if (skip_first and i == 0) or not line.strip() or not line.startswith(old):
            continue
        lines[i] = new + line[len(old) :]
    return "".join(lines)


def _leading(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _numbered(content: str, center: int, radius: int = 3) -> str:
    """Lines around ``center`` with line numbers, so the model sees what it actually wrote."""
    lines = content.splitlines()
    first, last = max(center - radius, 1), min(center + radius, len(lines))
    return "\n".join(
        f"{number:>4} | {lines[number - 1][:MAX_SHOWN_LINE_CHARS]}" for number in range(first, last + 1)
    )


def _undefined_names(source: str) -> dict[str, int]:
    """Undefined names and the first line using each, as found by pyflakes."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return {}
    found: dict[str, int] = {}
    for message in pyflakes_checker.Checker(tree).messages:
        if isinstance(message, pyflakes_messages.UndefinedName):
            found.setdefault(message.message_args[0], message.lineno)
    return found


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
