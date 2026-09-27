"""Unknown tools or invalid arguments produce a clear error without executing an action."""

import pytest

from coding_harness.domain import ModelResponse, StopReason, ToolCall
from coding_harness.toolset import ActionRejected

from helpers import call, done, make_controller, snapshot


def run_single(repo, reply):
    before = snapshot(repo)
    controller, model, env = make_controller(repo, [reply, done()])
    outcome = controller.run_task("task")
    tool_message = model.requests[1][-1]
    assert snapshot(repo) == before, "nothing may be written"
    assert env.commands == [], "nothing may be executed"
    assert outcome.stats.denied == 1 and outcome.stats.executed == 0
    return tool_message["content"]


def test_unknown_tool(repo):
    message = run_single(repo, call("delete_everything", path="."))
    assert message.startswith("DENIED (nothing was executed)")
    assert "unknown tool 'delete_everything'" in message
    assert "available tools: list_files" in message


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (call("read_file"), "path: Field required"),
        (call("edit_file", path="shop/pricing.py", old_text=1, new_text="x"), "old_text: Input should be a valid string"),
        (call("write_file", path="a.py", content="x", mode="append"), "mode: Extra inputs are not permitted"),
        (call("run_check", name="rm -rf /"), "name: Input should be 'tests'"),
        (call("read_file", path="shop/pricing.py", start_line=0), "start_line: Input should be greater than or equal to 1"),
        (ModelResponse(tool_calls=[ToolCall("read_file", "shop/pricing.py")]), "must be a JSON object"),
    ],
)
def test_invalid_arguments(repo, reply, expected):
    message = run_single(repo, reply)
    assert message.startswith("DENIED")
    assert expected in message


def test_path_outside_repository_is_denied(repo):
    message = run_single(repo, call("write_file", path="../secret.txt", content="pwned"))
    assert "outside the repository" in message
    assert (repo.parent / "secret.txt").read_text() == "host secret"


def test_validate_action_raises_for_unknown_tool(repo):
    controller, _, _ = make_controller(repo, [])
    with pytest.raises(ActionRejected, match="unknown tool"):
        controller.validate_action(ToolCall("shell", {"cmd": "ls"}))


def test_run_check_is_not_offered_without_configured_checks(repo):
    controller, _, _ = make_controller(repo, [call("run_check", name="tests"), done()], checks={})
    outcome = controller.run_task("task")
    assert "run_check" not in controller.registry.names
    assert outcome.stats.denied == 1


def test_denied_limit_stops_the_run(repo):
    controller, model, _ = make_controller(
        repo, [call("shell", cmd=f"ls {i}") for i in range(10)], max_denied=3
    )
    outcome = controller.run_task("task")
    assert outcome.stop_reason is StopReason.DENIED_LIMIT
    assert outcome.stats.denied == 3
    assert len(model.requests) == 3
