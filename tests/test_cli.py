"""The interface: select a project, inspect sessions, review a result. Only printing, no logic."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from coding_harness import harness
from coding_harness.cli import app
from coding_harness.config import load_config
from coding_harness.model_client import ScriptedModelClient

from helpers import FakeEnvironment, call, done, make_task

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def settings(git_repo: Path, tmp_path: Path) -> Path:
    path = tmp_path / "harness.toml"
    path.write_text(f'workspace_dir = "runs"\n\n[projects.shop]\nsource = "{git_repo.as_posix()}"\n'
                    'commit = "HEAD"\n\n[projects.shop.checks]\ntests = ["pytest", "-q"]\n')  # fmt: skip
    return path


def finished_session(settings: Path) -> str:
    replies = [call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text=""), done("fixed")]
    report = harness.run_task(load_config(settings), make_task(), model=ScriptedModelClient(replies),
                              env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip
    return report.session_id


def invoke(*args: str):
    return runner.invoke(app, list(args))


def test_projects_lists_only_the_configured_projects(settings):
    result = invoke("projects", "-c", str(settings))
    assert result.exit_code == 0 and "shop" in result.output and "checks: tests" in result.output


def test_sessions_status_and_show_read_a_saved_session(settings):
    session_id = finished_session(settings)

    listed = invoke("sessions", "-c", str(settings))
    assert session_id in listed.output and "finished" in listed.output and "fix total" in listed.output

    status = invoke("status", session_id, "-c", str(settings))
    assert status.exit_code == 0
    assert "status: finished" in status.output and "steps 2/30" in status.output and "actions 1/40" in status.output

    shown = invoke("show", session_id, "-c", str(settings))
    assert shown.exit_code == 0
    assert "M  shop/pricing.py" in shown.output and "VERIFIED" in shown.output


def test_unknown_project_and_session_are_usage_errors(settings):
    assert invoke("run", "-t", "fix the total", "--allow", "shop/**", "-p", "nope", "-c", str(settings)).exit_code == 2
    assert invoke("status", "no-such-session", "-c", str(settings)).exit_code == 2
    assert invoke("resume", "no-such-session", "-c", str(settings)).exit_code == 2


def test_a_finished_session_is_neither_resumed_nor_cancelled(settings):
    session_id = finished_session(settings)
    resumed = invoke("resume", session_id, "-c", str(settings))
    assert resumed.exit_code == 2 and "finished" in resumed.output
    cancelled = invoke("cancel", session_id, "-c", str(settings))
    assert cancelled.exit_code == 2 and "not running" in cancelled.output


def test_a_task_in_words_needs_an_allowed_scope(settings):
    result = invoke("run", "-t", "fix the total", "-c", str(settings))
    assert result.exit_code == 2 and "--allow" in result.output
