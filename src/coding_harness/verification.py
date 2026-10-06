"""Final verification: run acceptance and regression checks, collect the diff.

This runs regardless of what the model claims. A failed or unavailable check stays
visible and the run is not reported as verified.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coding_harness import lint
from coding_harness.domain import CommandResult
from coding_harness.sandbox import ExecutionEnvironment
from coding_harness.tools import in_scope
from coding_harness.workspace import Workspace

# Exit codes with which podman/docker report that the container itself could not run.
ENGINE_FAILURE_CODES = {125, 126, 127}


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class CheckKind(StrEnum):
    ACCEPTANCE = "acceptance"  # proves the task is solved
    REGRESSION = "regression"  # proves existing behaviour still works
    QUALITY = "quality"  # the change introduces no lint problems
    SCOPE = "scope"  # the change touches only the allowed files


@dataclass(frozen=True)
class CheckResult:
    name: str
    result: CommandResult
    kind: CheckKind = CheckKind.ACCEPTANCE

    @property
    def status(self) -> CheckStatus:
        if self.result.error is not None or self.result.exit_code in ENGINE_FAILURE_CODES:
            return CheckStatus.UNAVAILABLE
        return CheckStatus.PASSED if self.result.passed else CheckStatus.FAILED


@dataclass(frozen=True)
class VerificationReport:
    checks: list[CheckResult]
    changed_files: list[tuple[str, str]]
    diff: str

    @property
    def has_acceptance(self) -> bool:
        return any(c.kind is CheckKind.ACCEPTANCE for c in self.checks)

    @property
    def all_passed(self) -> bool:
        return bool(self.checks) and all(c.status is CheckStatus.PASSED for c in self.checks)

    @property
    def verified(self) -> bool:
        # Passing regression tests alone do not show that the task was solved.
        return self.has_acceptance and self.all_passed


class Verification:
    def __init__(
        self,
        env: ExecutionEnvironment,
        acceptance: Mapping[str, list[str]],
        regression: Mapping[str, list[str]],
        *,
        lint: bool = False,
        allowed_paths: Sequence[str] = (),
    ) -> None:
        self.env = env
        self.lint = lint
        self.allowed_paths = tuple(allowed_paths)
        self.checks = [(name, list(cmd), CheckKind.ACCEPTANCE) for name, cmd in acceptance.items()]
        self.checks += [(name, list(cmd), CheckKind.REGRESSION) for name, cmd in regression.items()]

    def run_acceptance_checks(self) -> list[CheckResult]:
        """Run the acceptance checks, then the regression checks."""
        return [CheckResult(name, self.env.run(command), kind) for name, command, kind in self.checks]

    def run_lint(self, workspace: Workspace) -> CheckResult:
        """Lint the changed Python files; only problems the change introduced count.

        Runs in the harness process: the code is only parsed, never imported or executed.
        """
        started = time.monotonic()
        paths = [p for status, p in workspace.changed_files() if status != "D" and p.endswith(".py")]
        found = []
        for path in paths:
            with (workspace.repo / path).open(encoding="utf-8", errors="replace", newline="") as handle:
                after = handle.read()
            found += [f"{path}:{p}" for p in lint.new_problems(workspace.original(path), after)]
        output = "\n".join(found) if found else f"no new lint problems in {len(paths)} changed Python file(s)"
        result = CommandResult(command=["lint", *paths], exit_code=1 if found else 0, output=output,
                               duration=time.monotonic() - started)  # fmt: skip
        return CheckResult("lint", result, CheckKind.QUALITY)

    def run_scope_check(self, workspace: Workspace) -> CheckResult:
        """Every changed file must match an allowed path, whatever the file tools allowed.

        This also catches changes the tools did not make, and a scope that was never configured
        for the run.
        """
        started = time.monotonic()
        outside = [p for _, p in workspace.changed_files() if not in_scope(p, self.allowed_paths)]
        output = (
            "changed outside the allowed scope:\n" + "\n".join(outside)
            if outside
            else "all changed files are inside the allowed scope: " + ", ".join(self.allowed_paths)
        )
        result = CommandResult(command=["scope", *self.allowed_paths], exit_code=1 if outside else 0,
                               output=output, duration=time.monotonic() - started)  # fmt: skip
        return CheckResult("scope", result, CheckKind.SCOPE)

    def show_diff(self, workspace: Workspace) -> tuple[list[tuple[str, str]], str]:
        return workspace.changed_files(), workspace.diff()

    def run(self, workspace: Workspace) -> VerificationReport:
        checks = self.run_acceptance_checks()
        if self.lint:
            checks.append(self.run_lint(workspace))
        if self.allowed_paths:
            checks.append(self.run_scope_check(workspace))
        changed, diff = self.show_diff(workspace)
        return VerificationReport(checks=checks, changed_files=changed, diff=diff)
