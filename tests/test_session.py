"""Session storage: everything about a task can be read back after the application restarts."""

import pytest

from coding_harness import harness
from coding_harness.domain import StopReason
from coding_harness.model_client import ScriptedModelClient
from coding_harness.session import SessionError, SessionStatus, SessionStore
from coding_harness.verification import CheckStatus

from helpers import FakeEnvironment, call, done, make_config, make_task


def test_task_steps_history_errors_and_checks_survive_a_restart(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    model = ScriptedModelClient([
        call("edit_file", path="shop/pricing.py", old_text="not in the file", new_text="x"),  # fails
        call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text=""),
        done("fixed"),
    ])  # fmt: skip
    report = harness.run_task(config, make_task(scope="only pricing"), model=model,
                              env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip

    # A new store instance knows nothing but the directory, as after a restart.
    session = SessionStore(config.workspace_dir).open(report.session_id)
    record = session.task()
    assert record["task"]["request"] == "fix total" and record["task"]["scope"] == "only pricing"
    assert record["task"]["allowed_paths"] == ["shop/**"] and record["commit"] == report.workspace.commit
    assert record["project_config"]["checks"] == {"tests": ["pytest", "-q"]}

    checkpoint = session.checkpoint()
    assert checkpoint.status is SessionStatus.FINISHED and checkpoint.stop_reason == "completed"
    assert checkpoint.step == 3 and checkpoint.stats.failed == 1 and checkpoint.stats.actions == 2
    errors = [m["content"] for m in checkpoint.messages if str(m.get("content")).startswith("ERROR:")]
    assert len(errors) == 1 and "old_text was not found" in errors[0]

    kinds = [e.kind for e in session.events()]
    assert kinds.count("tool_call") == kinds.count("tool_result") == 2
    assert session.uncertain_actions() == []

    loaded = harness.load_report(config, report.session_id)
    assert [(c.name, c.status) for c in loaded.verification.checks] == \
        [(c.name, c.status) for c in report.verification.checks]  # fmt: skip
    assert loaded.verified and loaded.outcome.stop_reason is StopReason.COMPLETED
    assert "-    return sum(prices) - 1  # bug" in loaded.verification.diff


def test_sessions_are_listed_and_unknown_ids_rejected(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    report = harness.run_task(config, make_task(), model=ScriptedModelClient([done()]),
                              env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip
    store = SessionStore(config.workspace_dir)
    assert [s.id for s in store.list()] == [report.session_id]
    for bad in ["nope", "../runs", ""]:
        with pytest.raises(SessionError):
            store.open(bad)


def test_a_failed_check_result_is_saved_as_failed(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path, limits={"max_fix_rounds": 0})
    report = harness.run_task(config, make_task(), model=ScriptedModelClient([done()]),
                              env_factory=lambda repo, mounts: FakeEnvironment(exit_code=1, output="1 failed"))  # fmt: skip
    loaded = harness.load_report(config, report.session_id)
    assert loaded.verification.checks[0].status is CheckStatus.FAILED and not loaded.verified
