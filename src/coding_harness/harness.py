"""Wires the components together for one session and writes the trace."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from coding_harness.config import ConfigError, HarnessConfig, LimitsConfig, ProjectConfig, TaskSpec
from coding_harness.context import ContextManager
from coding_harness.controller import AgentController, AgentOutcome
from coding_harness.domain import CommandResult, Event, RunStats, StopReason
from coding_harness.model_client import ModelClient, create_model_client
from coding_harness.navigation import Navigator
from coding_harness.prompts import SYSTEM_PROMPT, build_task_message, fix_message, resume_message
from coding_harness.review import Review, Reviewer, feedback_message
from coding_harness.sandbox import ContainerSandbox, ExecutionEnvironment, Mount
from coding_harness.session import (
    Checkpoint,
    Session,
    SessionError,
    SessionStatus,
    SessionStore,
    seen_from_list,
    seen_to_list,
)
from coding_harness.toolset import build_toolset, check_names
from coding_harness.tools import RepositoryTools, in_scope
from coding_harness.verification import CheckKind, CheckResult, CheckStatus, Verification, VerificationReport
from coding_harness.workspace import Workspace, WorkspaceError, _git

EnvFactory = Callable[[Path, Sequence[Mount]], ExecutionEnvironment]
Start = Callable[[AgentController, RepositoryTools], AgentOutcome]


class ResumeRefused(Exception):
    """A resume found a mismatch. The session is now paused; nothing was executed."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class RunReport:
    task: TaskSpec
    workspace: Workspace
    outcome: AgentOutcome | None  # None for a baseline run without the agent
    verification: VerificationReport
    trace_path: Path
    reviews: list[Review] = field(default_factory=list)  # advisory; never part of `verified`
    status: SessionStatus = SessionStatus.FINISHED
    uncertain: list[dict[str, Any]] = field(default_factory=list)  # interrupted, never replayed

    @property
    def verified(self) -> bool:
        return self.status is SessionStatus.FINISHED and self.verification.verified

    @property
    def session_id(self) -> str:
        return self.workspace.run_dir.name


def run_task(
    config: HarnessConfig,
    task: TaskSpec,
    *,
    model: ModelClient | None = None,
    reviewer_model: ModelClient | None = None,
    env_factory: EnvFactory | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> RunReport:
    """Start a new session: a fresh copy, the task saved with it, then the agent and the checks."""
    name, project = config.project(task.project)
    workspace = Workspace.create(project.source, task.commit or project.commit, config.workspace_dir)
    session = Session(workspace.run_dir)
    session.save_task({
        "task": task.model_dump(mode="json"),
        "project": name,
        "project_config": project.model_dump(mode="json"),
        "commit": workspace.commit,
        "limits": config.limits.model_dump(),
        "created": datetime.now().isoformat(timespec="seconds"),
    })  # fmt: skip
    message = _task_message(config, task, project)

    with EventLog(session.events_path, forward=on_event) as log:
        log(Event("run_started", 0, {"session": session.id, "task": task.request, "project": name,
                                     "commit": workspace.commit, "model": config.model.name}))  # fmt: skip
        return _drive(config, task, project, workspace, session, config.limits, log,
                      lambda controller, _: controller.run_task(message, controller.context.initial_context(task.request)),
                      model=model, reviewer_model=reviewer_model, env_factory=env_factory)  # fmt: skip


def resume_task(
    config: HarnessConfig,
    session_id: str,
    *,
    accept_working_copy: bool = False,
    model: ModelClient | None = None,
    reviewer_model: ModelClient | None = None,
    env_factory: EnvFactory | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> RunReport:
    """Continue a cancelled or paused session from its last checkpoint, after checking that it is safe.

    Checked again: the project is still allowed with the same settings, HEAD is the starting
    commit, the working copy is exactly as at the checkpoint (the harness's own edits included),
    and every changed file is inside the allowed paths. Any mismatch pauses the session and raises
    ResumeRefused. The limits saved with the task apply, and the consumed budget carries over.
    Interrupted actions are reported to the model and never replayed.
    """
    session = SessionStore(config.workspace_dir).open(session_id)
    record = session.task()
    task = TaskSpec.model_validate(record["task"])
    checkpoint = session.checkpoint()
    if checkpoint is None:
        raise SessionError(f"session {session_id} has no checkpoint to resume from")
    if checkpoint.status is SessionStatus.FINISHED:
        raise SessionError(f"session {session_id} is finished; see `coding-harness show {session_id}`")
    if (pid := session.running_pid()) is not None:
        raise SessionError(f"session {session_id} is still running (pid {pid}); cancel it first")

    with EventLog(session.events_path, forward=on_event) as log:
        log(Event("resume_requested", checkpoint.step, {"session": session_id, "status": str(checkpoint.status),
                                                        "accept_working_copy": accept_working_copy}))  # fmt: skip
        project, workspace, reasons = _revalidate(config, session, record, task, checkpoint, accept_working_copy, log)
        if reasons:
            session.save_checkpoint(replace(checkpoint, status=SessionStatus.PAUSED, note="; ".join(reasons)))
            log(Event("paused", checkpoint.step, {"reasons": reasons}))
            raise ResumeRefused(reasons)

        interrupted = session.uncertain_actions()
        for action in interrupted:
            log(Event("interrupted_action", checkpoint.step, {**action, "decision": "not replayed"}))
        messages = checkpoint.messages or [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _task_message(config, task, project)},
        ]
        saved = AgentOutcome(StopReason.INTERRUPTED, "", checkpoint.stats, messages, session.events(),
                             checkpoint.step, seen_from_list(checkpoint.seen))  # fmt: skip
        note = resume_message(interrupted, accepted_changes=accept_working_copy)

        def start(controller: AgentController, repo_tools: RepositoryTools) -> AgentOutcome:
            # Files changed before the interruption: lint still compares with the starting commit.
            for status, path in workspace.changed_files():
                if status != "D":
                    repo_tools.adopt(path, workspace.original(path))
            return controller.continue_task(saved, note)

        limits = LimitsConfig.model_validate(record["limits"])
        return _drive(config, task, project, workspace, session, limits, log, start,
                      model=model, reviewer_model=reviewer_model, env_factory=env_factory)  # fmt: skip


def _revalidate(
    config: HarnessConfig,
    session: Session,
    record: dict[str, Any],
    task: TaskSpec,
    checkpoint: Checkpoint,
    accept_working_copy: bool,
    log: Callable[[Event], None],
) -> tuple[ProjectConfig | None, Workspace | None, list[str]]:
    """Everything a resume must re-check. Returns the reasons to pause; an empty list means safe."""
    try:
        _, project = config.project(record["project"])  # the project the session started on, by name
    except ConfigError as exc:
        return None, None, [f"permission: {exc}"]
    if project.model_dump(mode="json") != record["project_config"]:
        return project, None, ["permission: the project's settings (source, commit, image or checks) changed "
                               "since the session started"]  # fmt: skip
    try:
        workspace = Workspace.open(session.dir, record["commit"])
    except WorkspaceError as exc:
        return project, None, [f"working copy: {exc}"]

    reasons = []
    if workspace.fingerprint() != checkpoint.fingerprint:
        if accept_working_copy:
            log(Event("working_copy_accepted", checkpoint.step, {"checkpoint": checkpoint.fingerprint,
                                                                 "now": workspace.fingerprint()}))  # fmt: skip
        else:
            writes = [a["name"] for a in session.uncertain_actions() if a.get("writes")]
            why = f" (interrupted write: {', '.join(writes)})" if writes else " (changed outside the harness?)"
            reasons.append("working copy: it differs from the last checkpoint" + why + "; inspect "
                           f"{workspace.repo}, then resume with --accept-working-copy to continue from it")
    outside = [p for _, p in workspace.changed_files() if not in_scope(p, task.allowed_paths)]
    if outside:  # never accepted: the scope is a permission, not a preference
        reasons.append(f"permission: files changed outside the allowed paths: {', '.join(outside)}")
    return project, workspace, reasons


def _drive(
    config: HarnessConfig,
    task: TaskSpec,
    project: ProjectConfig,
    workspace: Workspace,
    session: Session,
    limits: LimitsConfig,
    log: Callable[[Event], None],
    start: Start,
    *,
    model: ModelClient | None,
    reviewer_model: ModelClient | None,
    env_factory: EnvFactory | None,
) -> RunReport:
    """The agent, then review and edit-test-fix rounds, then the final checks. Ctrl+C cancels."""
    env_factory = env_factory or _container_factory(config, project)
    repo_tools = RepositoryTools(
        workspace.repo,
        max_write_chars=limits.max_write_chars,
        max_list_entries=limits.max_list_entries,
        max_search_matches=limits.max_search_matches,
        allowed_paths=task.allowed_paths,
    )
    # The agent's environment has no acceptance mount: it never sees the acceptance check.
    lint = config.verification.lint
    agent_env = env_factory(workspace.repo, [])
    navigator = Navigator(repo_tools, lambda args: _git(args, cwd=workspace.repo),
                          max_output_chars=limits.tool_output_chars)  # fmt: skip
    registry = build_toolset(repo_tools, agent_env, project.checks, lint=lint, navigator=navigator,
                             start_commit=workspace.commit)  # fmt: skip
    context = ContextManager(config.context, navigator)

    def checkpoint(messages, stats, step, seen) -> None:
        session.save_checkpoint(Checkpoint(SessionStatus.RUNNING, step, stats, messages, seen_to_list(seen),
                                           workspace.fingerprint()))  # fmt: skip

    controller = AgentController(model or create_model_client(config.model), registry, limits, log, checkpoint,
                                 context)  # fmt: skip
    reviews: list[Review] = []
    session.mark_running()
    try:
        try:
            outcome = start(controller, repo_tools)
            if outcome.stop_reason is not StopReason.INTERRUPTED and config.review.enabled:
                reviewer = Reviewer(reviewer_model or create_model_client(config.review.model or config.model))
                outcome = _review_loop(config, task, workspace, controller, outcome, reviewer, reviews, log)
            agent_checks = Verification(agent_env, {}, project.checks, lint=lint)
            outcome = _fix_loop(agent_checks, limits.max_fix_rounds, workspace, controller, outcome, log)
            if outcome.stop_reason is StopReason.INTERRUPTED:
                raise KeyboardInterrupt
            log(Event("verification", 0))
            verification = _verification(config, task, workspace, env_factory).run(workspace)
        except KeyboardInterrupt:
            return _cancelled(task, workspace, session, reviews, log)
        log(Event("verification_done", 0, {
            "verified": verification.verified,
            "checks": {c.name: str(c.status) for c in verification.checks},
            "changed_files": verification.changed_files,
        }))  # fmt: skip
        # Every action completed: the checkpoint now describes the final working copy.
        session.save_checkpoint(Checkpoint(SessionStatus.FINISHED, outcome.step, outcome.stats, outcome.messages,
                                           seen_to_list(outcome.seen), workspace.fingerprint(),
                                           str(outcome.stop_reason)))  # fmt: skip
        return _finish(RunReport(task, workspace, outcome, verification, session.trace_path, reviews))
    finally:
        session.mark_stopped()


def _task_message(config: HarnessConfig, task: TaskSpec, project: ProjectConfig) -> str:
    return build_task_message(task.request, task.scope, check_names(project.checks, config.verification.lint),
                              task.allowed_paths)  # fmt: skip


def _cancelled(
    task: TaskSpec, workspace: Workspace, session: Session, reviews: list[Review], log: Callable[[Event], None]
) -> RunReport:
    """Stop at once: no further actions, no final checks. The last checkpoint stays the resume point.

    Its fingerprint is deliberately not refreshed: if the interrupted action changed the working
    copy, a resume sees the difference and pauses.
    """
    checkpoint = session.checkpoint() or Checkpoint(SessionStatus.RUNNING, 0, RunStats(), [], [],
                                                    workspace.fingerprint())  # fmt: skip
    checkpoint = replace(checkpoint, status=SessionStatus.CANCELLED, stop_reason=str(StopReason.INTERRUPTED),
                         note="cancelled by the user")  # fmt: skip
    session.save_checkpoint(checkpoint)
    uncertain = session.uncertain_actions()
    log(Event("cancelled", checkpoint.step, {"uncertain_actions": uncertain}))
    outcome = AgentOutcome(StopReason.INTERRUPTED, "cancelled by the user", checkpoint.stats, checkpoint.messages,
                           session.events(), checkpoint.step, seen_from_list(checkpoint.seen))  # fmt: skip
    # Not checked, only shown: what changed so far.
    verification = VerificationReport([], workspace.changed_files(), workspace.diff())
    return _finish(RunReport(task, workspace, outcome, verification, session.trace_path, reviews,
                             SessionStatus.CANCELLED, uncertain))  # fmt: skip


def cancel_session(config: HarnessConfig, session_id: str) -> int:
    """Ask the process that runs a session to stop, as Ctrl+C in its terminal would. Returns its pid."""
    session = SessionStore(config.workspace_dir).open(session_id)
    pid = session.running_pid()
    if pid is None:
        raise SessionError(f"session {session_id} is not running")
    if os.name == "nt":
        raise SessionError("on Windows, cancel a run with Ctrl+C in its terminal")
    os.kill(pid, signal.SIGINT)
    return pid


def load_report(config: HarnessConfig, session_id: str) -> RunReport:
    """The saved report of a session, read back from its trace (after a restart, too)."""
    session = SessionStore(config.workspace_dir).open(session_id)
    if not session.trace_path.exists():
        raise SessionError(f"session {session_id} has no report yet (status: "
                           f"{getattr(session.checkpoint(), 'status', 'unknown')})")  # fmt: skip
    trace = json.loads(session.trace_path.read_text(encoding="utf-8"))
    names = {f.name for f in fields(CommandResult)}
    checks = [CheckResult(c["name"], CommandResult(**{k: v for k, v in c.items() if k in names}), CheckKind(c["kind"]))
              for c in trace["checks"]]  # fmt: skip
    verification = VerificationReport(checks, [tuple(c) for c in trace["changed_files"]], trace["diff"])
    outcome = None
    if trace["stop_reason"] is not None:
        outcome = AgentOutcome(StopReason(trace["stop_reason"]), trace["model_final_message"],
                               RunStats(**trace["stats"]), trace["messages"])  # fmt: skip
    workspace = Workspace(session.dir, session.repo, trace["commit"])
    return RunReport(TaskSpec.model_validate(trace["task"]), workspace, outcome, verification, session.trace_path,
                     status=SessionStatus(trace["status"]), uncertain=trace["uncertain"])  # fmt: skip


def _review_loop(
    config: HarnessConfig,
    task: TaskSpec,
    workspace: Workspace,
    controller: AgentController,
    outcome: AgentOutcome,
    reviewer: Reviewer,
    reviews: list[Review],
    log: Callable[[Event], None],
) -> AgentOutcome:
    """Review each proposed completion; send findings back at most ``max_rounds`` times.

    Only a completed run with a non-empty diff is reviewed. The last review is recorded
    without another round, so the user sees what the reviewer thought of the final diff.
    """
    rounds = 0
    while outcome.stop_reason is StopReason.COMPLETED and (diff := workspace.diff()):
        log(Event("review", 0, {"round": len(reviews) + 1}))
        review = reviewer.review(task.request, task.scope, diff)
        reviews.append(review)
        log(Event("review_done", 0, {"status": review.status, "findings": [str(f) for f in review.findings],
                                     "error": review.error}))  # fmt: skip
        if review.approve is not False or rounds >= config.review.max_rounds:
            break
        rounds += 1
        outcome = controller.continue_task(outcome, feedback_message(review))
    return outcome


def _fix_loop(
    checks: Verification,
    max_rounds: int,
    workspace: Workspace,
    controller: AgentController,
    outcome: AgentOutcome,
    log: Callable[[Event], None],
) -> AgentOutcome:
    """Edit-test-fix: after each proposed completion, run the agent's own checks.

    Failures go back to the model at most ``max_rounds`` times (counted over the whole run). Stops
    when every check passes, when the rounds are used up (checks_failed), when a check cannot run
    at all (checks_unavailable: the model cannot fix that), or when the agent stops for another
    reason. The final verification runs afterwards in every case.
    """
    while outcome.stop_reason is StopReason.COMPLETED and checks.checks:
        log(Event("agent_checks", 0, {"round": outcome.stats.fix_rounds + 1}))
        results = checks.run(workspace).checks
        log(Event("agent_checks_done", 0, {"checks": {c.name: str(c.status) for c in results}}))
        failed = [c for c in results if c.status is not CheckStatus.PASSED]
        if not failed:
            break
        # The model's own claim stays in final_message; the stop reason says why it does not hold.
        if any(c.status is CheckStatus.UNAVAILABLE for c in failed):
            return replace(outcome, stop_reason=StopReason.CHECKS_UNAVAILABLE)
        if outcome.stats.fix_rounds >= max_rounds:
            return replace(outcome, stop_reason=StopReason.CHECKS_FAILED)
        outcome.stats.fix_rounds += 1
        outcome = controller.continue_task(outcome, fix_message([(c.name, c.result.describe()) for c in failed]))
    return outcome


def run_baseline(config: HarnessConfig, task: TaskSpec, *, env_factory: EnvFactory | None = None) -> RunReport:
    """Run the task's final checks on the untouched starting commit (the acceptance check should fail)."""
    _, project = config.project(task.project)
    env_factory = env_factory or _container_factory(config, project)
    workspace = Workspace.create(project.source, task.commit or project.commit, config.workspace_dir)
    verification = _verification(config, task, workspace, env_factory).run(workspace)
    return _finish(RunReport(task, workspace, None, verification, workspace.run_dir / "trace.json"))


class EventLog:
    """Appends each event to a JSON Lines file the moment it happens, then forwards it.

    trace.json is only written at the end; this log survives a crash in the middle of a run.
    """

    def __init__(self, path: Path, forward: Callable[[Event], None] | None = None) -> None:
        self._handle = path.open("a", encoding="utf-8")
        self._forward = forward

    def __call__(self, event: Event) -> None:
        self._handle.write(json.dumps(asdict(event), default=str) + "\n")
        self._handle.flush()
        if self._forward is not None:
            self._forward(event)

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._handle.close()


def _container_factory(config: HarnessConfig, project: ProjectConfig) -> EnvFactory:
    sandbox = config.sandbox_for(project)
    return lambda repo, mounts: ContainerSandbox(sandbox, repo, mounts)


def _verification(config: HarnessConfig, task: TaskSpec, workspace: Workspace, env_factory: EnvFactory) -> Verification:
    mounts = []
    if config.verification.acceptance_dir is not None:
        mounts.append(Mount(config.verification.acceptance_dir, "/acceptance", read_only=True))
    return Verification(env_factory(workspace.repo, mounts), task.acceptance, task.regression,
                        lint=config.verification.lint, allowed_paths=task.allowed_paths)  # fmt: skip


def _finish(report: RunReport) -> RunReport:
    outcome = report.outcome
    trace = {
        "session": report.session_id,
        "status": str(report.status),
        "uncertain": report.uncertain,
        "task": report.task.model_dump(mode="json"),
        "commit": report.workspace.commit,
        "repo": str(report.workspace.repo),
        "stop_reason": str(outcome.stop_reason) if outcome else None,
        "model_final_message": outcome.final_message if outcome else None,
        "stats": asdict(outcome.stats) if outcome else None,
        "verified": report.verified,
        "checks": [
            {"name": c.name, "kind": str(c.kind), "status": str(c.status), **asdict(c.result)}
            for c in report.verification.checks
        ],
        "reviews": [asdict(r) | {"status": r.status} for r in report.reviews],
        "changed_files": report.verification.changed_files,
        "diff": report.verification.diff,
        "events": [asdict(e) for e in outcome.events] if outcome else [],
        "messages": outcome.messages if outcome else [],
    }
    report.trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    return report
