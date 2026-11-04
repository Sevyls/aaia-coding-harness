"""Safe resume: continue from the saved working copy, recheck permissions and limits, never replay."""

import json

import pytest

from coding_harness import harness
from coding_harness.domain import StopReason
from coding_harness.model_client import ScriptedModelClient
from coding_harness.session import SessionError, SessionStatus, SessionStore

from helpers import FakeEnvironment, call, done, make_config, make_task

FIX = call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text="")


def fake_env(repo, mounts):
    return FakeEnvironment()


class InterruptedCommand(FakeEnvironment):
    def run(self, command, *, timeout=None):
        raise KeyboardInterrupt


def cancelled(git_repo, tmp_path, replies=(FIX, KeyboardInterrupt()), env_factory=fake_env, **sections):
    """A session that made the given requests and was then cancelled."""
    config = make_config(git_repo, tmp_path, **sections)
    report = harness.run_task(config, make_task(), model=ScriptedModelClient(list(replies)), env_factory=env_factory)
    assert report.status is SessionStatus.CANCELLED
    return config, report.session_id, report.workspace.repo


def resume(config, session_id, replies, **options):
    model = ScriptedModelClient(replies)
    return harness.resume_task(config, session_id, model=model, env_factory=fake_env, **options), model


def test_resume_continues_from_the_saved_working_copy(git_repo, tmp_path):
    config, session_id, repo = cancelled(git_repo, tmp_path)

    report, model = resume(config, session_id, [done("fixed")])

    note = model.requests[0][-1]
    assert note["role"] == "user" and "resumed" in note["content"]
    assert "sum(prices) - 1" not in (repo / "shop" / "pricing.py").read_text()  # the earlier edit is kept
    assert report.status is SessionStatus.FINISHED and report.verified
    assert report.outcome.stats.actions == 1 and report.outcome.stats.model_calls == 3  # counted across both
    assert SessionStore(config.workspace_dir).open(session_id).checkpoint().status is SessionStatus.FINISHED


def test_consumed_limits_carry_over_and_the_saved_limits_apply(git_repo, tmp_path):
    config, session_id, _ = cancelled(git_repo, tmp_path, limits={"max_steps": 3})
    config.limits.max_steps = 50  # raising the limit afterwards does not give the session a new budget

    report, model = resume(config, session_id, [call("list_files"), call("list_files"), done()])

    assert report.outcome.stop_reason is StopReason.STEP_LIMIT
    # Step 1 made the edit, step 2 was the request cut off by Ctrl+C: it counts. Only step 3 is left.
    assert len(model.requests) == 1


def test_a_change_outside_the_harness_pauses_the_session(git_repo, tmp_path):
    config, session_id, repo = cancelled(git_repo, tmp_path)
    (repo / "shop" / "pricing.py").write_text("def total(prices):\n    return 0\n")

    with pytest.raises(harness.ResumeRefused, match="differs from the last checkpoint"):
        resume(config, session_id, [done()])

    checkpoint = SessionStore(config.workspace_dir).open(session_id).checkpoint()
    assert checkpoint.status is SessionStatus.PAUSED and "differs" in checkpoint.note

    # The user looked at the change and accepts it: now the session continues from it.
    report, model = resume(config, session_id, [done()], accept_working_copy=True)
    assert "accepted" in model.requests[0][-1]["content"] and report.status is SessionStatus.FINISHED


def test_an_interrupted_write_with_a_changed_working_copy_is_not_replayed(git_repo, tmp_path):
    config, session_id, repo = cancelled(git_repo, tmp_path)
    session = SessionStore(config.workspace_dir).open(session_id)
    # As if Ctrl+C hit while edit_file ran: the call is logged, the file changed, no result was logged.
    with session.events_path.open("a") as log:
        log.write(json.dumps({"kind": "tool_call", "step": 2, "data": {
            "op": "2.1", "name": "edit_file", "writes": True,
            "arguments": {"path": "shop/pricing.py", "old_text": "sum", "new_text": "max"}}}) + "\n")  # fmt: skip
    (repo / "shop" / "pricing.py").write_text("def total(prices):\n    return max(prices)\n")

    with pytest.raises(harness.ResumeRefused, match="interrupted write: edit_file"):
        resume(config, session_id, [done()])
    assert "max(prices)" in (repo / "shop" / "pricing.py").read_text()  # left as it is for the user


def test_an_interrupted_check_is_reported_to_the_model_not_replayed(git_repo, tmp_path):
    envs = iter([InterruptedCommand()])
    config, session_id, _ = cancelled(git_repo, tmp_path, replies=[call("run_check", name="tests")],
                                      env_factory=lambda repo, mounts: next(envs))  # fmt: skip

    report, model = resume(config, session_id, [done()])

    note = model.requests[0][-1]["content"]
    assert "did NOT" in note and "run_check" in note
    assert report.outcome.stats.actions == 1  # the interrupted check was not run again by the harness
    assert SessionStore(config.workspace_dir).open(session_id).uncertain_actions() == []


def test_changed_project_permissions_pause_the_session(git_repo, tmp_path):
    config, session_id, _ = cancelled(git_repo, tmp_path)
    config.projects["shop"].checks = {"tests": ["sh", "-c", "anything"]}

    with pytest.raises(harness.ResumeRefused, match="permission"):
        resume(config, session_id, [done()])


def test_a_project_that_is_no_longer_allowed_pauses_the_session(git_repo, tmp_path):
    config, session_id, _ = cancelled(git_repo, tmp_path)
    config.projects = {"other": config.projects["shop"]}

    with pytest.raises(harness.ResumeRefused, match="permission: unknown project"):
        resume(config, session_id, [done()])


def test_a_file_outside_the_allowed_paths_is_never_accepted(git_repo, tmp_path):
    config, session_id, repo = cancelled(git_repo, tmp_path)
    (repo / "README.md").write_text("changed\n")

    with pytest.raises(harness.ResumeRefused, match="outside the allowed paths: README.md"):
        resume(config, session_id, [done()], accept_working_copy=True)


def test_a_finished_session_cannot_be_resumed(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    report = harness.run_task(config, make_task(), model=ScriptedModelClient([done()]), env_factory=fake_env)
    with pytest.raises(SessionError, match="finished"):
        resume(config, report.session_id, [done()])
