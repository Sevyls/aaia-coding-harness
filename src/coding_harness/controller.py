"""Agent controller: ask the model, validate each tool request, execute, return results."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from coding_harness.config import LimitsConfig
from coding_harness.domain import (
    Event,
    ModelResponse,
    RunStats,
    StopReason,
    ToolCall,
    ToolResult,
    ToolStatus,
)
from coding_harness.model_client import Message, ModelClient, ModelError
from coding_harness.output import shorten
from coding_harness.prompts import SYSTEM_PROMPT
from coding_harness.toolset import FINISH, ActionRejected, Tool, ToolRegistry
from coding_harness.tools import PermissionDenied, ToolError


@dataclass
class AgentOutcome:
    stop_reason: StopReason
    final_message: str  # the model's own claim; verification decides whether it holds
    stats: RunStats
    messages: list[Message]
    events: list[Event] = field(default_factory=list)


class AgentController:
    def __init__(
        self,
        model: ModelClient,
        registry: ToolRegistry,
        limits: LimitsConfig,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        self.limits = limits
        self._on_event = on_event

    def run_task(self, task_message: str) -> AgentOutcome:
        self._stats = RunStats()
        self._events: list[Event] = []
        self._step = 0
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_message},
        ]
        try:
            reason, final = self._loop(messages)
        except KeyboardInterrupt:
            reason, final = StopReason.INTERRUPTED, "stopped by the user"
        self._emit("stop", reason=str(reason), message=final)
        return AgentOutcome(reason, final, self._stats, messages, self._events)

    def validate_action(self, call: ToolCall) -> tuple[Tool, BaseModel]:
        """Check that the tool exists and the arguments fit its schema. Raises ActionRejected."""
        return self.registry.validate(call)

    def _loop(self, messages: list[Message]) -> tuple[StopReason, str]:
        limits, stats = self.limits, self._stats
        last_signature, repeats = None, 0
        tools = self.registry.specs()

        for self._step in range(1, limits.max_steps + 1):
            response = self._ask_model(messages, tools)
            if response is None:
                return StopReason.MODEL_ERROR, "the model could not be reached"
            messages.append(_assistant_message(response))
            self._count_tokens(response)
            self._emit(
                "model_reply",
                content=response.content,
                tool_calls=[{"name": c.name, "arguments": c.arguments} for c in response.tool_calls],
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            if not response.tool_calls:
                return StopReason.COMPLETED, response.content

            for call in response.tool_calls:
                if stats.actions >= limits.max_actions:
                    self._emit("limit", limit="max_actions", value=limits.max_actions)
                    return StopReason.ACTION_LIMIT, f"stopped after {stats.actions} actions"
                stats.actions += 1

                signature = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                repeats = repeats + 1 if signature == last_signature else 1
                last_signature = signature
                if limits.max_repeated_actions and repeats > limits.max_repeated_actions:
                    self._emit("limit", limit="max_repeated_actions", value=limits.max_repeated_actions)
                    return StopReason.REPEAT_LIMIT, f"the same {call.name!r} request repeated {repeats} times"

                result = self._execute(call)
                messages.append({"role": "tool", "tool_name": call.name, "content": _tool_text(result)})

                if call.name == FINISH and result.status is ToolStatus.OK:
                    return StopReason.COMPLETED, call.arguments.get("summary", "")
                if stats.denied >= limits.max_denied:
                    self._emit("limit", limit="max_denied", value=limits.max_denied)
                    return StopReason.DENIED_LIMIT, f"stopped after {stats.denied} denied actions"

        self._emit("limit", limit="max_steps", value=limits.max_steps)
        return StopReason.STEP_LIMIT, f"stopped after {limits.max_steps} model requests"

    def _ask_model(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse | None:
        attempts = self.limits.max_model_retries + 1
        for attempt in range(1, attempts + 1):
            self._stats.model_calls += 1
            self._emit("model_request", attempt=attempt)
            try:
                return self.model.request_action(messages, tools)
            except ModelError as exc:
                self._emit("model_error", error=str(exc), attempt=attempt)
                if attempt < attempts:
                    self._stats.retries += 1
        return None

    def _count_tokens(self, response: ModelResponse) -> None:
        stats = self._stats
        stats.prompt_tokens += response.prompt_tokens or 0
        stats.completion_tokens += response.completion_tokens or 0
        stats.max_prompt_tokens = max(stats.max_prompt_tokens, response.prompt_tokens or 0)

    def _execute(self, call: ToolCall) -> ToolResult:
        self._emit("tool_call", name=call.name, arguments=call.arguments)
        try:
            tool, args = self.validate_action(call)
            content = tool.handler(args)
        except (ActionRejected, PermissionDenied) as exc:
            self._stats.denied += 1
            result = ToolResult(ToolStatus.DENIED, str(exc))
        except ToolError as exc:
            self._stats.executed += 1
            self._stats.failed += 1
            result = ToolResult(ToolStatus.ERROR, str(exc))
        except Exception as exc:  # a bug in a tool must not crash the run
            self._stats.executed += 1
            self._stats.failed += 1
            result = ToolResult(ToolStatus.ERROR, f"internal tool error: {type(exc).__name__}: {exc}")
        else:
            self._stats.executed += 1
            text, shortened = shorten(content, self.limits.tool_output_chars)
            result = ToolResult(ToolStatus.OK, text, shortened)
        self._emit("tool_result", name=call.name, status=str(result.status), content=result.content,
                   shortened=result.shortened)  # fmt: skip
        return result

    def _emit(self, kind: str, **data: Any) -> None:
        event = Event(kind, self._step, data)
        self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)


def _assistant_message(response: ModelResponse) -> Message:
    message: Message = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments if isinstance(c.arguments, dict) else {}}}
            for c in response.tool_calls
        ]
    return message


def _tool_text(result: ToolResult) -> str:
    if result.status is ToolStatus.DENIED:
        return f"DENIED (nothing was executed): {result.content}"
    if result.status is ToolStatus.ERROR:
        return f"ERROR: {result.content}"
    return result.content
