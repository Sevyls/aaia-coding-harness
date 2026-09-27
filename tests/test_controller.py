"""A scripted model reply calls the correct tool and receives its result."""

from coding_harness.domain import StopReason

from helpers import call, done, make_controller


def test_scripted_reply_calls_tool_and_receives_result(repo):
    controller, model, _ = make_controller(repo, [call("read_file", path="shop/pricing.py"), done("found it")])

    outcome = controller.run_task("fix the total")

    assert outcome.stop_reason is StopReason.COMPLETED
    assert outcome.final_message == "found it"
    # The second model request carries the tool result as the latest message.
    tool_message = model.requests[1][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "read_file"
    assert "return sum(prices) - 1" in tool_message["content"]
    assert outcome.stats.executed == 1 and outcome.stats.denied == 0


def test_tool_specs_are_sent_with_every_request(repo):
    controller, model, _ = make_controller(repo, [done()])
    specs = controller.registry.specs()
    names = [s["function"]["name"] for s in specs]
    assert names == ["list_files", "read_file", "search", "edit_file", "write_file", "run_check", "finish"]
    run_check = specs[names.index("run_check")]["function"]["parameters"]
    assert run_check["properties"]["name"]["enum"] == ["tests"]


def test_edit_and_run_configured_check(repo):
    replies = [
        call("edit_file", path="shop/pricing.py", old_text="sum(prices) - 1  # bug", new_text="sum(prices)"),
        call("run_check", name="tests"),
        call("finish", summary="removed the off-by-one"),
    ]
    controller, model, env = make_controller(repo, replies)

    outcome = controller.run_task("fix the total")

    assert "sum(prices)\n" in (repo / "shop" / "pricing.py").read_text()
    assert env.commands == [["pytest", "-q"]]
    assert "exit code: 0" in model.requests[2][-1]["content"]
    assert outcome.stop_reason is StopReason.COMPLETED
    assert outcome.final_message == "removed the off-by-one"


def test_finish_ends_the_loop_without_further_model_calls(repo):
    controller, model, _ = make_controller(repo, [call("finish", summary="nothing to do"), done("unused")])
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.COMPLETED
    assert len(model.requests) == 1


def test_progress_events_are_emitted(repo):
    events = []
    controller, _, _ = make_controller(repo, [call("list_files"), done()])
    controller._on_event = events.append

    controller.run_task("task")

    kinds = [e.kind for e in events]
    assert kinds == ["model_request", "model_reply", "tool_call", "tool_result",
                     "model_request", "model_reply", "stop"]  # fmt: skip
