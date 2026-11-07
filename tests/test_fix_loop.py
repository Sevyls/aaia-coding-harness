"""Edit-test-fix loop: failed checks after a proposed completion go back to the model, bounded."""

from coding_harness import harness
from coding_harness.domain import CommandResult, StopReason
from coding_harness.model_client import ScriptedModelClient
from coding_harness.verification import CheckStatus

from helpers import FakeEnvironment, call, done, make_config, make_task

FIX = call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text="")


class SequenceEnvironment(FakeEnvironment):
    """Returns the given (exit_code, output) results in order, then the last one forever."""

    def __init__(self, *results: tuple[int | None, str], error: str | None = None):
        super().__init__(error=error)
        self.results = list(results)

    def run(self, command, *, timeout=None):
        self.commands.append(list(command))
        exit_code, output = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return CommandResult(command=list(command), exit_code=exit_code, output=output, error=self.error)


def run(git_repo, tmp_path, agent_env, replies, max_fix_rounds=3):
    envs = iter([agent_env, FakeEnvironment()])  # the agent's checks first, then the final verification
    model = ScriptedModelClient(replies)
    config = make_config(git_repo, tmp_path, limits={"max_fix_rounds": max_fix_rounds})
    report = harness.run_task(config, make_task(), model=model, env_factory=lambda repo, mounts: next(envs))
    return report, model


def test_failed_check_guides_the_next_edit_until_it_passes(git_repo, tmp_path):
    agent_env = SequenceEnvironment((1, "FAILED test_total - assert 5 == 6"), (0, "1 passed"))
    report, model = run(git_repo, tmp_path, agent_env, [FIX, done("fixed"), call("list_files"), done("now fixed")])

    feedback = model.requests[2][-1]
    assert feedback["role"] == "user" and "assert 5 == 6" in feedback["content"]
    assert report.outcome.stop_reason is StopReason.COMPLETED
    assert report.outcome.stats.fix_rounds == 1
    assert report.verified


def test_fix_rounds_are_bounded(git_repo, tmp_path):
    agent_env = SequenceEnvironment((1, "still failing"))
    replies = [FIX, done(), done(), done()]
    report, model = run(git_repo, tmp_path, agent_env, replies, max_fix_rounds=2)

    assert report.outcome.stop_reason is StopReason.CHECKS_FAILED
    assert report.outcome.stats.fix_rounds == 2
    assert len(model.requests) == 4  # first attempt plus two fix rounds, then the harness stops


def test_unavailable_check_stops_at_once_and_never_counts_as_passed(git_repo, tmp_path):
    agent_env = SequenceEnvironment((None, ""), error="could not start 'podman'")
    report, model = run(git_repo, tmp_path, agent_env, [FIX, done()])

    assert report.outcome.stop_reason is StopReason.CHECKS_UNAVAILABLE
    assert len(model.requests) == 2  # nothing sent back: the model cannot fix a missing container engine
    assert report.outcome.stats.fix_rounds == 0


def test_no_checks_configured_means_no_fix_loop(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    config.projects["shop"].checks = {}
    report = harness.run_task(config, make_task(), model=ScriptedModelClient([FIX, done()]),
                              env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip
    assert report.outcome.stop_reason is StopReason.COMPLETED
    assert all(c.status is CheckStatus.PASSED for c in report.verification.checks)
