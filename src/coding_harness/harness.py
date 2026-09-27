"""Wires the components together for one run and writes the trace."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from coding_harness.config import HarnessConfig
from coding_harness.controller import AgentController, AgentOutcome
from coding_harness.domain import Event
from coding_harness.model_client import ModelClient, create_model_client
from coding_harness.prompts import build_task_message
from coding_harness.sandbox import ContainerSandbox, ExecutionEnvironment, Mount
from coding_harness.toolset import build_toolset
from coding_harness.tools import RepositoryTools
from coding_harness.verification import Verification, VerificationReport
from coding_harness.workspace import Workspace

EnvFactory = Callable[[Path, Sequence[Mount]], ExecutionEnvironment]


@dataclass
class RunReport:
    task: str | None
    workspace: Workspace
    outcome: AgentOutcome | None  # None for a baseline run without the agent
    verification: VerificationReport
    trace_path: Path

    @property
    def verified(self) -> bool:
        return self.verification.verified


def run_task(
    config: HarnessConfig,
    task: str,
    *,
    model: ModelClient | None = None,
    env_factory: EnvFactory | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> RunReport:
    env_factory = env_factory or _container_factory(config)
    workspace = Workspace.create(config.target.source, config.target.commit, config.workspace_dir)
    limits = config.limits
    repo_tools = RepositoryTools(
        workspace.repo,
        max_write_chars=limits.max_write_chars,
        max_list_entries=limits.max_list_entries,
        max_search_matches=limits.max_search_matches,
    )
    # The agent's environment has no acceptance mount: it never sees the acceptance check.
    registry = build_toolset(repo_tools, env_factory(workspace.repo, []), config.checks)
    controller = AgentController(model or create_model_client(config.model), registry, limits, on_event)

    task_message = build_task_message(task, config.target.scope, list(config.checks))
    outcome = controller.run_task(task_message)
    if on_event:
        on_event(Event("verification", 0))
    verification = _verification(config, workspace, env_factory).run(workspace)
    return _finish(RunReport(task, workspace, outcome, verification, workspace.run_dir / "trace.json"))


def run_baseline(config: HarnessConfig, *, env_factory: EnvFactory | None = None) -> RunReport:
    """Run the final checks on the untouched starting commit (the acceptance check should fail)."""
    env_factory = env_factory or _container_factory(config)
    workspace = Workspace.create(config.target.source, config.target.commit, config.workspace_dir)
    verification = _verification(config, workspace, env_factory).run(workspace)
    return _finish(RunReport(None, workspace, None, verification, workspace.run_dir / "trace.json"))


def _container_factory(config: HarnessConfig) -> EnvFactory:
    return lambda repo, mounts: ContainerSandbox(config.sandbox, repo, mounts)


def _verification(config: HarnessConfig, workspace: Workspace, env_factory: EnvFactory) -> Verification:
    mounts = []
    if config.verification.acceptance_dir is not None:
        mounts.append(Mount(config.verification.acceptance_dir, "/acceptance", read_only=True))
    v = config.verification
    return Verification(env_factory(workspace.repo, mounts), v.acceptance, v.regression)


def _finish(report: RunReport) -> RunReport:
    outcome = report.outcome
    trace = {
        "task": report.task,
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
        "changed_files": report.verification.changed_files,
        "diff": report.verification.diff,
        "events": [asdict(e) for e in outcome.events] if outcome else [],
        "messages": outcome.messages if outcome else [],
    }
    report.trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    return report
