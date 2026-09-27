"""Test doubles: scripted replies and a fake execution environment."""

from __future__ import annotations

from pathlib import Path

from coding_harness.config import LimitsConfig
from coding_harness.controller import AgentController
from coding_harness.domain import CommandResult, ModelResponse, ToolCall
from coding_harness.model_client import ScriptedModelClient
from coding_harness.toolset import build_toolset
from coding_harness.tools import RepositoryTools


def call(tool: str, /, **arguments) -> ModelResponse:
    """A model reply that requests one tool."""
    return ModelResponse(tool_calls=[ToolCall(tool, arguments)])


def done(text: str = "done") -> ModelResponse:
    """A model reply without a tool request, i.e. a completion claim."""
    return ModelResponse(content=text)


class FakeEnvironment:
    """Records commands and returns a fixed result instead of starting a container."""

    def __init__(self, exit_code: int | None = 0, output: str = "ok", error: str | None = None):
        self.exit_code, self.output, self.error = exit_code, output, error
        self.commands: list[list[str]] = []

    def run(self, command: list[str], *, timeout: float | None = None) -> CommandResult:
        self.commands.append(list(command))
        return CommandResult(command=list(command), exit_code=self.exit_code, output=self.output,
                             error=self.error)  # fmt: skip


def make_controller(
    repo: Path,
    replies,
    *,
    env: FakeEnvironment | None = None,
    checks: dict[str, list[str]] | None = None,
    repeat_last: bool = False,
    **limits,
) -> tuple[AgentController, ScriptedModelClient, FakeEnvironment]:
    env = env or FakeEnvironment()
    checks = {"tests": ["pytest", "-q"]} if checks is None else checks
    registry = build_toolset(RepositoryTools(repo), env, checks)
    model = ScriptedModelClient(replies, repeat_last=repeat_last)
    return AgentController(model, registry, LimitsConfig(**limits)), model, env


def snapshot(root: Path) -> dict[str, str]:
    """All file contents under root, to prove that nothing was written."""
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}
