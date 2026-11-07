"""Repository navigation: symbols, references, module dependencies, git status and a repo map.

Everything here is read-only and works on the source with ``ast``; no repository code is run.
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path

from coding_harness.output import MARKER, shorten
from coding_harness.tools import MAX_SEARCH_FILE_BYTES, MAX_SHOWN_LINE_CHARS, RepositoryTools, ToolError

Parsed = tuple[Path, str, ast.Module | None, str]  # file, source, tree, why it could not be parsed
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class Navigator:
    def __init__(
        self,
        tools: RepositoryTools,
        git: Callable[[list[str]], str],
        *,
        max_output_chars: int = 8_000,
    ) -> None:
        self.tools = tools
        # Runs git in the repository copy, e.g. ``lambda args: workspace._git(args, cwd=repo)``.
        self.git = git
        self.max_output_chars = max_output_chars

    def find_symbol(self, name: str) -> str:
        """Where ``name`` is defined: classes, functions and methods, module-level assignments."""
        found, broken = [], []
        for file, source, tree, error in self._parsed():
            if tree is None:
                broken.append(self._broken(file, error))
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, *FUNCTIONS)) and node.name == name:
                    found.append(self._line(file, source, node.lineno))
            for node in tree.body:
                if name in _assigned_names(node):
                    found.append(self._line(file, source, node.lineno))
        return self._matches(found, broken, f"no definition found for {name!r}")

    def find_references(self, name: str) -> str:
        """Where ``name`` is used: as a name, as an attribute, or in an import."""
        found, broken = [], []
        for file, source, tree, error in self._parsed():
            if tree is None:
                broken.append(self._broken(file, error))
                continue
            lines = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == name:
                    lines.add(node.lineno)
                elif isinstance(node, ast.Attribute) and node.attr == name:
                    lines.add(node.lineno)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if any(name in (alias.asname, *alias.name.split(".")) for alias in node.names):
                        lines.add(node.lineno)
            found += [self._line(file, source, number) for number in sorted(lines)]
        return self._matches(found, broken, f"no references found for {name!r}")

    def module_deps(self, path: str) -> str:
        """The modules ``path`` imports (resolved to repository files where possible) and the
        repository modules that import it."""
        target = self.tools.resolve(path)
        if target.suffix != ".py" or not target.is_file():
            raise ToolError(f"not a Python file: {path!r}")
        parsed = list(self._parsed())
        own = next((entry for entry in parsed if entry[0] == target), None)
        if own is None or own[2] is None:
            raise ToolError(f"{path!r} could not be parsed: {own[3] if own else 'too large'}")
        lines = [f"{self.tools.display(target)} imports:"]
        for module, resolved in self._imports(target, own[2]):
            where = self.tools.display(resolved) if resolved else "not in the repository"
            lines.append(f"  {module} -> {where}")
        if len(lines) == 1:
            lines.append("  (nothing)")
        users = [
            self.tools.display(file)
            for file, _, tree, _ in parsed
            if tree is not None and file != target
            and any(resolved == target for _, resolved in self._imports(file, tree))
        ]
        lines.append("imported by:")
        lines += [f"  {user}" for user in users] or ["  (no repository module)"]
        broken = [self._broken(file, error) for file, _, tree, error in parsed if tree is None]
        if broken:
            lines.append("could not be checked:")
            lines += [f"  {line}" for line in broken]
        return shorten("\n".join(lines), self.max_output_chars)[0]

    def git_status(self, start_commit: str) -> str:
        """HEAD compared with the start commit, then uncommitted changes."""
        head = self.git(["rev-parse", "HEAD"]).strip()
        if head == start_commit:
            where = f"HEAD is the start commit {start_commit[:12]}"
        else:
            count = self.git(["rev-list", "--count", f"{start_commit}..HEAD"]).strip()
            where = f"HEAD {head[:12]} is {count} commit(s) after the start commit {start_commit[:12]}"
        status = self.git(["status", "--short"]).rstrip() or "(no uncommitted changes)"
        stat = self.git(["diff", "--stat", start_commit]).rstrip() or "(no changes to tracked files)"
        text = f"{where}\n\ngit status --short:\n{status}\n\ngit diff --stat {start_commit[:12]}:\n{stat}"
        return shorten(text, self.max_output_chars)[0]

    def repo_map(self, max_chars: int) -> str:
        """One line per Python file with its top-level classes (and their methods) and functions."""
        lines = []
        for file, _, tree, error in self._parsed():
            if tree is None:
                lines.append(self._broken(file, error))
                continue
            parts = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [n.name for n in node.body if isinstance(n, FUNCTIONS)]
                    parts.append(f"class {node.name}" + (f"({', '.join(methods)})" if methods else ""))
                elif isinstance(node, FUNCTIONS):
                    parts.append(f"def {node.name}")
            shown = self.tools.display(file)
            lines.append(f"{shown}: {'; '.join(parts)}" if parts else shown)
        text = "\n".join(lines) if lines else "(no Python files)"
        # Leave room for the marker, so the result stays within max_chars.
        room = max(max_chars - len(MARKER) - 20, 0)
        return text if len(text) <= max_chars else shorten(text, room)[0]

    def _parsed(self) -> Iterator[Parsed]:
        for file in self.tools._walk(self.tools.root):
            if file.suffix != ".py":
                continue
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES:
                yield file, "", None, "too large"
                continue
            try:
                source = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                yield file, "", None, f"unreadable: {exc}"
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # e.g. invalid escape sequences
                try:
                    yield file, source, ast.parse(source), ""
                except SyntaxError as exc:
                    yield file, source, None, f"line {exc.lineno}: {exc.msg}"
                except ValueError as exc:  # e.g. NUL bytes
                    yield file, source, None, str(exc)

    def _imports(self, file: Path, tree: ast.Module) -> list[tuple[str, Path | None]]:
        """Each imported module with the repository file it maps to (None: stdlib, third party)."""
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [(alias.name, self._module_file(alias.name.split("."))) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module.split(".") if node.module else []
                if node.level:
                    # Relative: one level is the file's own package, each further level its parent.
                    package = file.parent.relative_to(self.tools.root).parts
                    if node.level - 1 > len(package):
                        found.append(("." * node.level + (node.module or ""), None))  # above the root
                        continue
                    base = [*package[: len(package) - (node.level - 1)], *base]
                label = "." * node.level + (node.module or "")
                for alias in node.names:
                    # ``from pkg import name`` may import the submodule pkg/name.py or a name in pkg.
                    submodule = self._module_file([*base, alias.name], src=not node.level)
                    if submodule:
                        found.append((label + ("" if label.endswith(".") else ".") + alias.name, submodule))
                    else:
                        found.append((label, self._module_file(base, src=not node.level)))
        return list(dict.fromkeys(found))  # a module imported in several places is listed once

    def _module_file(self, parts: list[str], src: bool = True) -> Path | None:
        """The repository file for a dotted module name; also tries a ``src/`` layout."""
        if not parts or not all(parts):
            return None
        for base in (self.tools.root, self.tools.root / "src") if src else (self.tools.root,):
            for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
                if candidate.is_file() and candidate.resolve().is_relative_to(self.tools.root):
                    return candidate
        return None

    def _line(self, file: Path, source: str, number: int) -> str:
        lines = source.splitlines()
        text = lines[number - 1].strip() if number <= len(lines) else ""
        return f"{self.tools.display(file)}:{number}: {text[:MAX_SHOWN_LINE_CHARS]}"

    def _broken(self, file: Path, error: str) -> str:
        return f"{self.tools.display(file)}: could not be parsed ({error})"

    def _matches(self, found: list[str], broken: list[str], empty: str) -> str:
        limit = self.tools.max_search_matches
        lines = found[:limit] or [empty]
        if len(found) > limit:
            lines.append(f"... stopped after {limit} matches")
        if broken:
            lines += ["files that could not be parsed:", *broken]
        return "\n".join(lines)


def _assigned_names(node: ast.stmt) -> set[str]:
    """Names bound by a module-level assignment (``x = ...``, ``x: int = ...``, ``a, b = ...``)."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return set()
    return {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}
