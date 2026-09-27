"""Model clients. The controller only depends on the ModelClient protocol."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from coding_harness.config import ModelConfig
from coding_harness.domain import ModelResponse, ToolCall

Message = dict[str, Any]


class ModelError(Exception):
    """The model could not be reached or returned something unusable."""


class ModelClient(Protocol):
    def request_action(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse: ...


class ScriptedModelClient:
    """Returns prepared replies in order. Used for repeatable tests without a live model.

    A reply may be an exception instance, which is raised instead. With ``repeat_last`` the
    final reply is returned forever, which simulates a model stuck in a loop.
    """

    def __init__(self, replies: Iterable[ModelResponse | Exception], repeat_last: bool = False):
        self._replies = list(replies)
        self._repeat_last = repeat_last
        self.requests: list[list[Message]] = []  # what the controller sent, for assertions

    def request_action(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.requests.append(copy.deepcopy(messages))
        if not self._replies:
            raise ModelError("scripted replies exhausted")
        reply = self._replies[0] if self._repeat_last and len(self._replies) == 1 else self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class OllamaClient:
    def __init__(self, config: ModelConfig, client: Any = None) -> None:
        if client is None:
            import ollama

            client = ollama.Client(host=config.host, timeout=config.request_timeout)
        self._client = client
        self._config = config

    def request_action(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        try:
            response = self._client.chat(
                model=self._config.name,
                messages=messages,
                tools=tools,
                think=self._config.think,
                options={"temperature": self._config.temperature},
            )
        except Exception as exc:  # connection refused, model not pulled, timeout, ...
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        message = response.message
        content = message.content or ""
        calls = [
            ToolCall(call.function.name, dict(call.function.arguments or {}))
            for call in message.tool_calls or []
        ]
        if not calls:
            # Small models often write tool calls as text instead of using the API field,
            # sometimes a whole plan at once. Only the first call is taken: the later ones
            # were written before the model saw any result.
            calls = parse_text_tool_calls(content)[:1]
        return ModelResponse(content=content, tool_calls=calls)


def parse_text_tool_calls(content: str) -> list[ToolCall]:
    """Recover {"name": ..., "arguments": {...}} objects written anywhere in the reply text.

    Covers bare JSON, JSON lines, ```json fences and <tool_call> tags.
    """
    decoder = json.JSONDecoder()
    calls, position = [], 0
    while (start := content.find("{", position)) != -1:
        try:
            data, position = decoder.raw_decode(content, start)
        except ValueError:
            position = start + 1
            continue
        if not isinstance(data, Mapping) or not isinstance(data.get("name"), str):
            continue
        arguments = data.get("arguments", data.get("parameters", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass  # left as a string; the controller rejects it with a clear error
        calls.append(ToolCall(data["name"], arguments))
    return calls


def create_model_client(config: ModelConfig) -> ModelClient:
    if config.provider == "ollama":
        return OllamaClient(config)
    raise ValueError(f"unsupported model provider: {config.provider}")
