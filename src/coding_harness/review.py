"""Second-model review of the diff, after the coding agent proposed completion.

The reviewer sees only the task, the scope and the diff, never the coding agent's own summary,
which is where false success claims live. It has no tools. Its verdict is untrusted model
output too: it is shown and recorded, and its findings may go back to the coding agent, but
it never decides whether a run is verified. Only the final checks do that.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from coding_harness.model_client import ModelClient, ModelError
from coding_harness.output import shorten

MAX_DIFF_CHARS = 12_000

REVIEW_PROMPT = """\
You review a code change that another agent made to solve a task. You see the task, the
allowed scope and the diff. You cannot run anything.

Report only concrete problems in the diff:
- the change does not do what the task asks, or does only part of it
- duplicated, dead or misplaced code (for example code between the imports)
- changes outside the allowed scope, or changed or deleted tests
Do not report style preferences or ideas for further work.

Reply with JSON only, no other text:
{"approve": true or false, "findings": [{"file": "path", "line": 0, "problem": "what is wrong"}]}
Approve with an empty findings list if there is no concrete problem.
"""


@dataclass(frozen=True)
class Finding:
    file: str
    line: int | None
    problem: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{where}: {self.problem}"


@dataclass(frozen=True)
class Review:
    approve: bool | None  # None: the review could not be done or not be read
    findings: list[Finding] = field(default_factory=list)
    raw: str = ""
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def status(self) -> str:
        return "unavailable" if self.approve is None else "approved" if self.approve else "changes requested"


class Reviewer:
    def __init__(self, model: ModelClient) -> None:
        self.model = model

    def review(self, task: str, scope: str, diff: str) -> Review:
        diff_text, _ = shorten(diff, MAX_DIFF_CHARS)
        messages = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": f"Task:\n{task.strip()}\n\nScope:\n{scope.strip() or '(none)'}"
                                        f"\n\nDiff:\n{diff_text}"},
        ]  # fmt: skip
        try:
            response = self.model.request_action(messages, [])
        except ModelError as exc:
            return Review(None, error=str(exc))
        tokens = {"prompt_tokens": response.prompt_tokens or 0, "completion_tokens": response.completion_tokens or 0}
        verdict = _first_json_object(response.content)
        if verdict is None or not isinstance(verdict.get("approve"), bool):
            return Review(None, raw=response.content, error="the reply contained no valid verdict", **tokens)
        findings = [_finding(f) for f in verdict.get("findings") or [] if isinstance(f, Mapping)]
        findings = [f for f in findings if f.problem]
        # A rejection without a single concrete finding gives the coding agent nothing to fix.
        approve = verdict["approve"] or not findings
        return Review(approve, findings, response.content, **tokens)


def feedback_message(review: Review) -> str:
    """The findings as a message to the coding agent."""
    listed = "\n".join(f"- {finding}" for finding in review.findings)
    return (
        "A reviewer read your diff and found problems:\n"
        f"{listed}\n"
        "Fix the ones that are real, run the checks again, then call finish. The reviewer can be "
        "wrong: if a finding is not a real problem, leave the code and say why in the summary."
    )


def _finding(data: Mapping) -> Finding:
    line = data.get("line")
    return Finding(
        file=str(data.get("file") or "?"),
        line=line if isinstance(line, int) and line > 0 else None,
        problem=str(data.get("problem") or "").strip(),
    )


def _first_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    position = 0
    while (start := text.find("{", position)) != -1:
        try:
            data, _ = decoder.raw_decode(text, start)
        except ValueError:
            position = start + 1
            continue
        if isinstance(data, dict):
            return data
        position = start + 1
    return None
