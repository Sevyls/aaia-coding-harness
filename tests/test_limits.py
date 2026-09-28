"""Action, step, repeat, output and retry limits stop the run or bound what the model sees."""

from coding_harness.domain import ModelResponse, StopReason, ToolCall
from coding_harness.model_client import ModelError
from coding_harness.output import shorten

from helpers import call, done, make_controller


def test_repeating_reply_reaches_action_limit(repo):
    controller, model, _ = make_controller(
        repo, [call("list_files")], repeat_last=True, max_actions=5, max_repeated_actions=0
    )

    outcome = controller.run_task("task")

    assert outcome.stop_reason is StopReason.ACTION_LIMIT
    assert outcome.stats.executed == 5  # the sixth request was not executed
    assert outcome.stats.actions == 5
    assert len(model.requests) == 6
    assert outcome.events[-2].kind == "limit"


def test_identical_requests_are_stopped_as_a_loop(repo):
    controller, _, _ = make_controller(repo, [call("list_files")], repeat_last=True, max_repeated_actions=3)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.REPEAT_LIMIT
    assert outcome.stats.executed == 3


def test_step_limit(repo):
    replies = [call("read_file", path="README.md", start_line=1, end_line=n) for n in range(1, 10)]
    controller, model, _ = make_controller(repo, replies, max_steps=3)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.STEP_LIMIT
    assert len(model.requests) == 3


def test_large_tool_output_is_bounded_and_marked(repo):
    (repo / "big.txt").write_text("line\n" * 5000)  # 25,000 characters
    controller, model, _ = make_controller(repo, [call("read_file", path="big.txt"), done()], tool_output_chars=1000)

    outcome = controller.run_task("task")

    content = model.requests[1][-1]["content"]
    assert "[... output shortened by the harness:" in content
    assert len(content) < 1100
    assert outcome.events[3].data["shortened"] is True


def test_shorten_keeps_head_and_tail():
    text, shortened = shorten("A" * 50 + "B" * 50, 20)
    assert shortened
    assert text.startswith("A" * 10) and text.endswith("B" * 10)
    assert "80 characters omitted" in text
    assert shorten("short", 20) == ("short", False)


def test_model_error_is_retried_and_counted(repo):
    controller, _, _ = make_controller(repo, [ModelError("connection refused"), done()], max_model_retries=2)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.COMPLETED
    assert outcome.stats.retries == 1


def test_model_errors_beyond_retry_limit_stop_the_run(repo):
    controller, _, _ = make_controller(repo, [ModelError("down")] * 3, max_model_retries=1)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.MODEL_ERROR
    assert outcome.stats.retries == 1
    assert outcome.stats.model_calls == 2


def test_token_usage_is_summed_and_largest_prompt_kept(repo):
    replies = [
        ModelResponse(tool_calls=[ToolCall("list_files", {})], prompt_tokens=1000, completion_tokens=20),
        ModelResponse(content="done", prompt_tokens=1500, completion_tokens=10),
    ]
    controller, _, _ = make_controller(repo, replies)

    stats = controller.run_task("task").stats

    assert (stats.prompt_tokens, stats.completion_tokens, stats.max_prompt_tokens) == (2500, 30, 1500)
