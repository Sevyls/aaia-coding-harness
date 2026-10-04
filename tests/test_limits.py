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


FAILING_EDIT = call("edit_file", path="shop/pricing.py", old_text="not in the file", new_text="x")


def test_alternating_read_and_failing_edit_is_stopped_as_a_loop(repo):
    # qwen2.5-coder:7b with 16k context: read, failing edit, read, ... until the step limit
    replies = [call("read_file", path="shop/pricing.py"), FAILING_EDIT] * 10
    controller, model, _ = make_controller(repo, replies, max_repeated_actions=3)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.REPEAT_LIMIT
    assert "without any change to the repository" in outcome.final_message
    assert outcome.stats.actions == 7  # read and edit 3 times each, the 4th read is not executed


def test_longer_cycles_are_stopped_too(repo):
    cycle = [call("list_files"), call("search", pattern="total"), call("read_file", path="README.md")]
    controller, _, _ = make_controller(repo, cycle * 10, max_repeated_actions=2)
    assert controller.run_task("task").stop_reason is StopReason.REPEAT_LIMIT


def test_a_repeated_request_is_told_that_repeating_does_not_help(repo):
    controller, model, _ = make_controller(repo, [FAILING_EDIT, FAILING_EDIT, done()])
    controller.run_task("task")
    first, second = model.requests[1][-1]["content"], model.requests[2][-1]["content"]
    assert "Repeating it will not help" not in first
    assert "you have now made exactly this request 2 times" in second
    assert "Applying the same edit again" in second  # an edit is a write, even a failing one


def test_edit_and_undo_cycles_are_stopped_as_a_loop(repo):
    # qwen2.5-coder:7b with 16k context: changed a line, changed it back, and so on
    edit = call("edit_file", path="shop/pricing.py", old_text="- 1", new_text="- 2")
    undo = call("edit_file", path="shop/pricing.py", old_text="- 2", new_text="- 1")
    controller, _, _ = make_controller(repo, [edit, undo] * 10, max_repeated_actions=3)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.REPEAT_LIMIT
    assert outcome.stats.actions == 7  # the repository is back where it was after every undo


def test_the_same_successful_edit_applied_again_and_again_is_a_loop(repo):
    # qwen2.5-coder:7b: new_text contained old_text, so the same edit matched again each time
    # and the file kept growing; the repository changed, but the request did not
    grow = call("edit_file", path="shop/pricing.py", old_text="def total(prices):",
                new_text="def total(prices):\n    pass\n\n\ndef total(prices):")  # fmt: skip
    controller, model, _ = make_controller(repo, [grow] * 10, max_repeated_actions=3)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.REPEAT_LIMIT
    assert outcome.stats.executed == 3 and "made 4 times during the run" in outcome.final_message
    assert "the file now contains the change more than once" in model.requests[2][-1]["content"]


def test_reading_again_after_an_edit_is_not_a_repeat(repo):
    edits = [call("edit_file", path="shop/pricing.py", old_text=f"- {n}", new_text=f"- {n + 1}") for n in range(1, 6)]
    replies = [step for edit in edits for step in (call("read_file", path="shop/pricing.py"), edit)]
    controller, model, _ = make_controller(repo, [*replies, done()], max_repeated_actions=1)
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.COMPLETED  # every read sees a changed file
    assert not any("Repeating it" in m["content"] for m in model.requests[-1] if m["role"] == "tool")


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
