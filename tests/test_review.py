"""A second model reviews the diff; its findings go back to the coder, but never decide `verified`."""

import json
from pathlib import Path

import pytest

from coding_harness import harness
from coding_harness.config import HarnessConfig
from coding_harness.domain import ModelResponse
from coding_harness.model_client import ModelError, ScriptedModelClient
from coding_harness.review import Reviewer

from helpers import FakeEnvironment, call, done

FIX = call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text="")


def verdict(approve: bool, *problems: str) -> ModelResponse:
    findings = [{"file": "shop/pricing.py", "line": 2, "problem": p} for p in problems]
    return ModelResponse(content=json.dumps({"approve": approve, "findings": findings}))


@pytest.mark.parametrize(
    ("reply", "approve", "findings"),
    [
        ('Here is my verdict:\n```json\n{"approve": false, "findings": [{"file": "a.py", "line": 3, '
         '"problem": "duplicated line"}]}\n```', False, ["a.py:3: duplicated line"]),
        ('{"approve": true, "findings": []}', True, []),
        ('{"approve": false, "findings": []}', True, []),  # nothing concrete to fix
        ("looks fine to me", None, []),  # no verdict: unavailable, not approved
    ],
)
def test_reviewer_reads_the_verdict(reply, approve, findings):
    review = Reviewer(ScriptedModelClient([ModelResponse(content=reply)])).review("task", "", "diff")
    assert review.approve is approve
    assert [str(f) for f in review.findings] == findings


def test_unreachable_reviewer_is_unavailable_not_approved():
    review = Reviewer(ScriptedModelClient([ModelError("connection refused")])).review("task", "", "diff")
    assert review.status == "unavailable" and "connection refused" in review.error


def make_config(source: Path, tmp_path: Path, max_rounds: int = 1) -> HarnessConfig:
    return HarnessConfig.model_validate({
        "workspace_dir": tmp_path / "runs",
        "target": {"source": source, "commit": "HEAD", "scope": "only shop/pricing.py"},
        "sandbox": {"image": "unused"},
        "verification": {"acceptance": {"acceptance": ["pytest", "/acceptance"]}},
        "review": {"enabled": True, "max_rounds": max_rounds},
    })  # fmt: skip


def run(git_repo, tmp_path, coder_replies, reviewer_replies, **config):
    coder, reviewer = ScriptedModelClient(coder_replies), ScriptedModelClient(reviewer_replies)
    report = harness.run_task(make_config(git_repo, tmp_path, **config), "fix total", model=coder,
                              reviewer_model=reviewer, env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip
    return report, coder, reviewer


def test_reviewer_sees_task_scope_and_diff_but_not_the_coders_claim(git_repo, tmp_path):
    report, _, reviewer = run(git_repo, tmp_path, [FIX, done("SECRET CLAIM: all tests pass")],
                              [verdict(True)])  # fmt: skip
    prompt = reviewer.requests[0][-1]["content"]
    assert "fix total" in prompt and "only shop/pricing.py" in prompt
    assert "-    return sum(prices) - 1  # bug" in prompt
    assert "SECRET CLAIM" not in json.dumps(reviewer.requests)
    assert [r.status for r in report.reviews] == ["approved"]


def test_findings_go_back_to_the_coder_once_then_the_final_diff_is_reviewed(git_repo, tmp_path):
    coder_replies = [FIX, done("fixed"),
                     call("edit_file", path="shop/pricing.py", old_text="sum(prices)", new_text="sum(prices) + 0"),
                     done("addressed the review")]  # fmt: skip
    report, coder, reviewer = run(git_repo, tmp_path, coder_replies,
                                  [verdict(False, "the fix is incomplete"), verdict(True)])  # fmt: skip

    feedback = coder.requests[2][-1]
    assert feedback["role"] == "user" and "shop/pricing.py:2: the fix is incomplete" in feedback["content"]
    assert [r.status for r in report.reviews] == ["changes requested", "approved"]
    assert report.outcome.final_message == "addressed the review"
    assert "sum(prices) + 0" in report.verification.diff
    assert report.outcome.stats.actions == 2  # one budget for the whole run, not one per round


def test_no_rounds_left_records_the_review_without_feedback(git_repo, tmp_path):
    report, coder, _ = run(git_repo, tmp_path, [FIX, done()], [verdict(False, "dislike")], max_rounds=0)
    assert [r.status for r in report.reviews] == ["changes requested"]
    assert len(coder.requests) == 2  # no third request with feedback


def test_review_never_decides_verified(git_repo, tmp_path):
    report, _, _ = run(git_repo, tmp_path, [FIX, done()], [verdict(False, "dislike")], max_rounds=0)
    assert report.verified  # the final checks passed; the reviewer's opinion is shown, not counted


def test_nothing_to_review_without_a_diff(git_repo, tmp_path):
    report, _, reviewer = run(git_repo, tmp_path, [done("nothing to do")], [verdict(True)])
    assert report.reviews == [] and reviewer.requests == []


def test_reviews_are_written_to_the_trace(git_repo, tmp_path):
    report, _, _ = run(git_repo, tmp_path, [FIX, done()], [verdict(True)])
    trace = json.loads(report.trace_path.read_text(encoding="utf-8"))
    assert trace["reviews"][0]["status"] == "approved"
