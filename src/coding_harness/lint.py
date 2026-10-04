"""Static lint check that counts only the problems a change introduces.

Uses pyflakes (unused imports, redefinitions, undefined names, ...) and pycodestyle's E402
(code before an import). Like the edit guardrails it only analyses the code, never runs it.
Style rules such as line length are left out: they say little about whether a fix is sloppy.
"""

from __future__ import annotations

import ast
import warnings
from collections import Counter
from dataclasses import dataclass

import pycodestyle
from pyflakes import checker as pyflakes_checker

PYCODESTYLE_RULES = ["E402"]


@dataclass(frozen=True)
class Problem:
    code: str  # pycodestyle code or pyflakes message class, e.g. "E402", "UnusedImport"
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.code} {self.message}"


def problems(source: str) -> list[Problem]:
    """All lint problems in ``source``; an unparsable file has none (the guardrail covers it)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return []
    found = [
        Problem(type(m).__name__, m.lineno, m.message % m.message_args)
        for m in pyflakes_checker.Checker(tree).messages
    ]
    found += _pycodestyle(source)
    return sorted(found, key=lambda p: (p.line, p.code))


def new_problems(before: str | None, after: str) -> list[Problem]:
    """Problems in ``after`` that ``before`` did not have, compared without line numbers.

    Line numbers shift with every edit, so a problem counts as old if the same code and
    message occurred before, as often as it occurs now.
    """
    old = Counter((p.code, p.message) for p in problems(before)) if before is not None else Counter()
    added = []
    for problem in problems(after):
        key = (problem.code, problem.message)
        if old[key]:
            old[key] -= 1
        else:
            added.append(problem)
    return added


class _Collect(pycodestyle.BaseReport):
    def __init__(self, options) -> None:
        super().__init__(options)
        self.found: list[Problem] = []

    def error(self, line_number, offset, text, check):
        code = text[:4]
        if code in self._selected:
            self.found.append(Problem(code, line_number, text[5:]))
        return code

    _selected = set(PYCODESTYLE_RULES)


def _pycodestyle(source: str) -> list[Problem]:
    style = pycodestyle.StyleGuide(select=PYCODESTYLE_RULES, quiet=True)
    report = _Collect(style.options)
    lines = source.splitlines(keepends=True)
    pycodestyle.Checker(lines=lines, options=style.options, report=report).check_all()
    return report.found
