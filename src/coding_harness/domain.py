"""Plain data types shared by the harness components."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """A tool request proposed by the model. Untrusted until the controller validates it."""

    name: str
    arguments: Any


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int | None = None  # as reported by the model server, if it reports them
    completion_tokens: int | None = None


class ToolStatus(StrEnum):
    OK = "ok"  # the action ran
    ERROR = "error"  # the action ran but failed (e.g. text to replace not found)
    DENIED = "denied"  # the action was rejected before anything was executed


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    content: str
    shortened: bool = False


@dataclass(frozen=True)
class CommandResult:
    """Evidence from one executed command: exact command, exit code and captured output."""

    command: list[str]
    exit_code: int | None  # None when the command could not be started
    output: str
    truncated: bool = False
    timed_out: bool = False
    duration: float = 0.0
    error: str | None = None  # why the command could not run at all
    executed_argv: list[str] | None = None  # full argv including the sandbox wrapper

    @property
    def passed(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0

    def describe(self) -> str:
        lines = [f"command: {shlex.join(self.command)}"]
        if self.error:
            lines.append(f"error: {self.error}")
        else:
            lines.append(f"exit code: {self.exit_code}")
        if self.timed_out:
            lines.append("timed out: the harness stopped the command")
        lines.append(f"duration: {self.duration:.1f}s")
        lines.append("output (shortened):" if self.truncated else "output:")
        lines.append(self.output or "(no output)")
        return "\n".join(lines)


class StopReason(StrEnum):
    COMPLETED = "completed"  # the model proposed completion; not proof of correctness
    STEP_LIMIT = "step_limit"
    ACTION_LIMIT = "action_limit"
    DENIED_LIMIT = "denied_limit"
    REPEAT_LIMIT = "repeat_limit"
    MODEL_ERROR = "model_error"
    INTERRUPTED = "interrupted"


@dataclass
class RunStats:
    model_calls: int = 0
    actions: int = 0  # every tool request, executed or not
    executed: int = 0
    denied: int = 0
    failed: int = 0
    retries: int = 0  # repeated model requests after a model error
    prompt_tokens: int = 0  # summed over all model requests
    completion_tokens: int = 0
    max_prompt_tokens: int = 0  # largest single prompt: how full the context window got


@dataclass(frozen=True)
class Event:
    """Progress notification for the interface and the trace."""

    kind: str
    step: int
    data: dict[str, Any] = field(default_factory=dict)
