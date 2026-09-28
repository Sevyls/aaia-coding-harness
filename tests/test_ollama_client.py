"""The Ollama client speaks the real wire format; tested against a mocked HTTP transport."""

import json

import httpx
import ollama
import pytest

from coding_harness.config import ModelConfig
from coding_harness.domain import ToolCall
from coding_harness.model_client import ModelError, OllamaClient, parse_text_tool_calls


def client_returning(message: dict, seen: list, config: ModelConfig | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = {"model": "m", "created_at": "2026-01-01T00:00:00Z", "message": message, "done": True,
                "prompt_eval_count": 1234, "eval_count": 56}  # fmt: skip
        return httpx.Response(200, json=body)

    return OllamaClient(config or ModelConfig(name="m"),
                        ollama.Client(host="http://ollama.test", transport=httpx.MockTransport(handler)))  # fmt: skip


def test_native_tool_calls_are_parsed_and_history_is_accepted():
    seen = []
    client = client_returning(
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
        seen,
    )  # fmt: skip
    history = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_files", "arguments": {}}}]},
        {"role": "tool", "tool_name": "list_files", "content": "a.py"},
    ]

    response = client.request_action(history, tools=[{"type": "function", "function": {"name": "read_file"}}])

    assert response.tool_calls == [ToolCall("read_file", {"path": "a.py"})]
    sent = seen[0]
    assert sent["model"] == "m" and sent["options"]["temperature"] == 0.0
    assert sent["messages"][2] == {"role": "tool", "tool_name": "list_files", "content": "a.py"}


def test_token_usage_is_reported_and_context_window_is_sent():
    seen = []
    client = client_returning({"role": "assistant", "content": "ok"}, seen,
                              ModelConfig(name="m", context_window=32768))  # fmt: skip

    response = client.request_action([], [])

    assert (response.prompt_tokens, response.completion_tokens) == (1234, 56)
    assert seen[0]["options"]["num_ctx"] == 32768


def test_context_window_is_left_to_the_server_by_default():
    seen = []
    client_returning({"role": "assistant", "content": "ok"}, seen).request_action([], [])
    assert "num_ctx" not in seen[0]["options"]


def test_tool_call_written_as_text_is_recovered():
    client = client_returning(
        {"role": "assistant", "content": '```json\n{"name": "search", "arguments": {"pattern": "total"}}\n```'}, []
    )
    assert client.request_action([], []).tool_calls == [ToolCall("search", {"pattern": "total"})]


def test_only_the_first_text_call_of_a_written_plan_is_taken():
    plan = '{"name": "list_files", "arguments": {}}\n{"name": "finish", "arguments": {"summary": "guessed"}}'
    client = client_returning({"role": "assistant", "content": plan}, [])
    assert client.request_action([], []).tool_calls == [ToolCall("list_files", {})]


def test_plain_answer_has_no_tool_calls():
    client = client_returning({"role": "assistant", "content": "The bug is fixed."}, [])
    response = client.request_action([], [])
    assert response.content == "The bug is fixed." and response.tool_calls == []


def test_connection_problems_become_model_errors():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = OllamaClient(ModelConfig(), ollama.Client(host="http://ollama.test", transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelError, match="Failed to connect"):
        client.request_action([], [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('<tool_call>{"name": "list_files", "arguments": {}}</tool_call>', [ToolCall("list_files", {})]),
        ('{"name": "read_file", "arguments": "{\\"path\\": \\"x\\"}"}', [ToolCall("read_file", {"path": "x"})]),
        ("I will now read the file.", []),
        (
            '{"name": "list_files", "arguments": {"path": "src"}}\n{"name": "finish", "arguments": {"summary": "x"}}',
            [ToolCall("list_files", {"path": "src"}), ToolCall("finish", {"summary": "x"})],
        ),
        ('Plan: {"name": "search", "arguments": {"pattern": "{"}} then more', [ToolCall("search", {"pattern": "{"})]),
        ("```json\n{not json}\n```", []),
    ],
)
def test_parse_text_tool_calls(text, expected):
    assert parse_text_tool_calls(text) == expected
