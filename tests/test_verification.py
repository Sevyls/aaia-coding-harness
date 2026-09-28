"""Failed or unavailable checks stay visible, even when the model claims it is done."""

import json
from pathlib import Path

import pytest

from coding_harness import harness
from coding_harness.config import HarnessConfig
from coding_harness.domain import CommandResult, StopReason
from coding_harness.verification import CheckKind, CheckResult, CheckStatus, VerificationReport

from helpers import FakeEnvironment, call, done
from coding_harness.model_client import ScriptedModelClient


def check(exit_code=0, error=None, timed_out=False, kind=CheckKind.ACCEPTANCE):
    result = CommandResult(["pytest"], exit_code, "out", error=error, timed_out=timed_out)
    return CheckResult("tests", result, kind)


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (check(0), CheckStatus.PASSED),
        (check(1), CheckStatus.FAILED),
        (check(0, timed_out=True), CheckStatus.FAILED),
        (check(None, error="could not start 'podman'"), CheckStatus.UNAVAILABLE),
        (check(125), CheckStatus.UNAVAILABLE),  # the engine could not start the container
    ],
)
def test_check_status(result, status):
    assert result.status is status


def test_report_is_only_verified_when_every_check_passed():
    regression = check(0, kind=CheckKind.REGRESSION)
    assert VerificationReport([check(0), regression], [], "").verified
    assert not VerificationReport([check(0), check(1)], [], "").verified
    assert not VerificationReport([check(0), check(None, error="x")], [], "").verified
    assert not VerificationReport([], [], "").verified  # nothing verified


def test_passing_regression_tests_alone_do_not_verify_the_task():
    report = VerificationReport([check(0, kind=CheckKind.REGRESSION)], [], "")
    assert report.all_passed and not report.has_acceptance
    assert not report.verified


def make_config(source: Path, tmp_path: Path) -> HarnessConfig:
    return HarnessConfig.model_validate({
        "workspace_dir": tmp_path / "runs",
        "target": {"source": source, "commit": "HEAD"},
        "sandbox": {"image": "unused"},
        "checks": {"tests": ["pytest", "-q"]},
        "verification": {"acceptance": {"acceptance": ["pytest", "/acceptance"]},
                         "regression": {"unit": ["pytest", "tests"]}},
    })  # fmt: skip


def test_failed_final_check_is_not_reported_as_passed(git_repo, tmp_path):
    env = FakeEnvironment(exit_code=1, output="FAILED test_total - assert 5 == 6")
    model = ScriptedModelClient([
        call("edit_file", path="shop/pricing.py", old_text="  # bug", new_text=""),
        done("All tests pass now, the bug is fixed."),
    ])  # fmt: skip

    report = harness.run_task(make_config(git_repo, tmp_path), "fix total", model=model,
                              env_factory=lambda repo, mounts: env)  # fmt: skip

    assert report.outcome.stop_reason is StopReason.COMPLETED  # the model claims success
    assert not report.verified  # ...but the evidence says otherwise
    assert [(c.kind, c.status) for c in report.verification.checks] == [
        (CheckKind.ACCEPTANCE, CheckStatus.FAILED),
        (CheckKind.REGRESSION, CheckStatus.FAILED),
    ]
    assert "assert 5 == 6" in report.verification.checks[0].result.output
    assert report.verification.changed_files == [("M", "shop/pricing.py")]
    assert report.trace_path.exists()


def test_acceptance_check_is_mounted_only_for_final_verification(git_repo, tmp_path):
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    config = make_config(git_repo, tmp_path)
    config.verification.acceptance_dir = acceptance
    seen_mounts = []

    def env_factory(repo, mounts):
        seen_mounts.append([(m.target, m.read_only) for m in mounts])
        return FakeEnvironment()

    report = harness.run_task(config, "task", model=ScriptedModelClient([done()]), env_factory=env_factory)

    # Agent environment first (no acceptance check), then verification (read-only mount).
    assert seen_mounts == [[], [("/acceptance", True)]]
    assert report.verified


def test_baseline_runs_checks_on_the_untouched_commit(git_repo, tmp_path):
    env = FakeEnvironment(exit_code=1, output="1 failed")
    report = harness.run_baseline(make_config(git_repo, tmp_path), env_factory=lambda repo, mounts: env)
    assert report.outcome is None
    assert report.verification.changed_files == []
    assert not report.verified


def test_events_are_logged_as_they_happen_and_survive_a_crash(git_repo, tmp_path):
    class BrokenVerificationEnv(FakeEnvironment):
        def run(self, command, *, timeout=None):
            raise RuntimeError("container engine crashed")

    envs = iter([FakeEnvironment(), BrokenVerificationEnv()])  # agent first, then verification
    model = ScriptedModelClient([call("list_files"), done("finished")])

    with pytest.raises(RuntimeError, match="crashed"):
        harness.run_task(make_config(git_repo, tmp_path), "task", model=model,
                         env_factory=lambda repo, mounts: next(envs))  # fmt: skip

    (run_dir,) = (tmp_path / "runs").iterdir()
    assert not (run_dir / "trace.json").exists()  # the summary is written only at the end...
    lines = (run_dir / "events.jsonl").read_text().splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds[0] == "run_started" and "tool_result" in kinds and kinds[-1] == "verification"
