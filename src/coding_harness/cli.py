"""Command-line interface: submit a task, show progress, changed files, diff and checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from coding_harness import harness
from coding_harness.config import ConfigError, HarnessConfig, TaskSpec, load_config, load_task
from coding_harness.domain import Event, StopReason
from coding_harness.session import SessionError, SessionStatus, SessionStore
from coding_harness.verification import CheckStatus
from coding_harness.workspace import WorkspaceError

app = typer.Typer(
    help="A small coding harness: a task goes in, a reviewable diff comes out.\n\n"
         "Settings come from harness.toml (copy harness.example.toml). "
         "Start with [bold]baseline[/bold], then [bold]run[/bold].",
    epilog="Docs: README.md · docs/guardrails.md · docs/windows.md",
    no_args_is_help=True, add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)  # fmt: skip
console = Console()

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Harness settings (TOML)")]
ProjectOption = Annotated[str | None, typer.Option("--project", "-p",
                                                   help="Project from \\[projects]; optional if only one")]
TaskFileOption = Annotated[Path | None, typer.Option(help="Task file (TOML), e.g. tasks/*.toml")]
STATUS_STYLE = {CheckStatus.PASSED: "green", CheckStatus.FAILED: "red", CheckStatus.UNAVAILABLE: "yellow"}


RUN_EPILOG = (
    "[bold]Examples[/bold]\n\n"
    "  coding-harness run --task-file tasks/cancel-allocation.toml\n\n"
    "  coding-harness run -t \"Reject qty <= 0\" --allow 'src/**' --model qwen2.5-coder:7b\n\n"
    "  coding-harness run --task-file tasks/invalid-quantity.toml --review-model qwen3.8:27b\n\n"
    "[bold]Exit codes[/bold]: 0 verified (acceptance check configured and every final check passed), "
    "1 not verified, cancelled or paused, 2 usage or setup error. Each session is kept under "
    ".harness/runs/<id>/; Ctrl+C cancels it, `resume <id>` continues it."
)


@app.command(epilog=RUN_EPILOG)
def run(
    task: Annotated[str | None, typer.Option("--task", "-t", help="The coding task, in words")] = None,
    task_file: TaskFileOption = None,
    allow: Annotated[list[str] | None, typer.Option(help="With --task: glob of files the agent may change "
                                                         "(repeatable)")] = None,
    project: ProjectOption = None,
    config: ConfigOption = Path("harness.toml"),
    model: Annotated[str | None, typer.Option(help="Override the model name from \\[model]")] = None,
    review: Annotated[bool | None, typer.Option("--review/--no-review",
                                                help="Turn the second-model review on or off")] = None,
    review_model: Annotated[str | None, typer.Option(help="Reviewer model name (turns review on)")] = None,
) -> None:
    # The help paragraph stays on one line: Typer shows line breaks in docstrings as they are.
    """Run the agent on a task in a fresh copy of the target repository.

    Give exactly one of --task (with --allow) or --task-file. Shows each step, then the changed files, the diff and the final checks, which run whatever the model claims.
    """
    spec = _task(task, task_file, allow, project)
    settings = _load(config)
    if model:
        settings.model.name = model
    if review_model:
        settings.review.enabled = True
        base = settings.review.model or settings.model
        settings.review.model = base.model_copy(update={"name": review_model})
    if review is not None:
        settings.review.enabled = review

    name, selected = _project(settings, spec)
    console.rule("[bold]Task")
    console.print(escape(spec.request.strip()))
    reviewer = (settings.review.model or settings.model).name if settings.review.enabled else "off"
    console.print(f"[dim]model {settings.model.name} · reviewer {reviewer} · project {name}"
                  f" @ {spec.commit or selected.commit}[/dim]")  # fmt: skip
    try:
        report = harness.run_task(settings, spec, on_event=_print_event)
    except WorkspaceError as exc:
        _fail(str(exc))
    _show_report(report)
    raise typer.Exit(0 if report.verified else 1)


@app.command(epilog="[bold]Example[/bold]: coding-harness baseline --task-file tasks/cancel-allocation.toml")
def baseline(task_file: TaskFileOption = None, config: ConfigOption = Path("harness.toml")) -> None:
    """Run a task's final checks on the untouched starting commit (the acceptance check should fail).

    Shows that the acceptance check reproduces the bug or missing feature before any change is made.
    """
    if task_file is None:
        raise typer.BadParameter("give --task-file")
    spec = _task(None, task_file, None, None)
    settings = _load(config)
    _project(settings, spec)
    try:
        report = harness.run_baseline(settings, spec)
    except WorkspaceError as exc:
        _fail(str(exc))
    _show_report(report)


SessionArg = Annotated[str, typer.Argument(help="Session id, from `coding-harness sessions`")]


@app.command()
def sessions(config: ConfigOption = Path("harness.toml")) -> None:
    """List saved sessions: status, step, task."""
    settings = _load(config)
    table = Table(show_header=True, header_style="bold")
    for column in ("session", "status", "step", "actions", "stop reason", "task"):
        table.add_column(column)
    for session in SessionStore(settings.workspace_dir).list():
        checkpoint = session.checkpoint()
        request = _one_line(session.task()["task"]["request"], 60)
        if checkpoint is None:
            table.add_row(session.id, "-", "0", "0", "-", escape(request))
            continue
        table.add_row(session.id, _status_text(checkpoint.status), str(checkpoint.step),
                      str(checkpoint.stats.actions), checkpoint.stop_reason or "-", escape(request))  # fmt: skip
    console.print(table)


@app.command()
def status(session_id: SessionArg, config: ConfigOption = Path("harness.toml")) -> None:
    """Show a session's progress: status, budget used, last steps and interrupted actions."""
    settings = _load(config)
    session = _session(settings, session_id)
    record, checkpoint = session.task(), session.checkpoint()
    console.print(f"[bold]{session.id}[/bold]  project {escape(record['project'])} @ {record['commit']}")
    console.print(escape(record["task"]["request"].strip()))
    if checkpoint is None:
        console.print("no checkpoint yet")
        return
    limits, stats = record["limits"], checkpoint.stats
    pid = session.running_pid()
    console.print(f"status: {_status_text(checkpoint.status)}" + (f" (pid {pid})" if pid else "")
                  + (f" · {escape(checkpoint.note)}" if checkpoint.note else "")
                  + f" · last checkpoint {checkpoint.saved_at}")  # fmt: skip
    console.print(f"budget used: steps {checkpoint.step}/{limits['max_steps']} · actions {stats.actions}/"
                  f"{limits['max_actions']} · denied {stats.denied}/{limits['max_denied']} · fix rounds "
                  f"{stats.fix_rounds}/{limits['max_fix_rounds']}")  # fmt: skip
    console.rule("[bold]Last events")
    for event in session.events()[-8:]:
        console.print(f"[dim]{event.step:>3}[/dim] {event.kind} {escape(_one_line(json.dumps(event.data, default=str), 120))}")
    if uncertain := session.uncertain_actions():
        console.rule("[bold yellow]Interrupted actions (outcome unknown, never replayed)")
        for action in uncertain:
            console.print(f"  {escape(action['name'])} {escape(_one_line(json.dumps(action.get('arguments')), 120))}")


@app.command()
def cancel(session_id: SessionArg, config: ConfigOption = Path("harness.toml")) -> None:
    """Cancel a running session from another terminal (like Ctrl+C in its own terminal)."""
    try:
        pid = harness.cancel_session(_load(config), session_id)
    except SessionError as exc:
        _fail(str(exc))
    console.print(f"sent the cancel signal to pid {pid}; the session stops after cleaning up")


@app.command(epilog="[bold]Example[/bold]: coding-harness resume 20261103-201404-4d11f2")
def resume(
    session_id: SessionArg,
    accept_working_copy: Annotated[bool, typer.Option(help="Continue from the working copy as it is now, "
                                                           "after you inspected a reported difference")] = False,
    model: Annotated[str | None, typer.Option(help="Override the model name from \\[model]")] = None,
    config: ConfigOption = Path("harness.toml"),
) -> None:
    """Continue a cancelled or paused session from its last checkpoint.

    Rechecks the project permissions, the allowed paths and the saved working copy first; on a mismatch the session is paused and nothing runs. Interrupted actions are never replayed.
    """
    settings = _load(config)
    if model:
        settings.model.name = model
    console.rule(f"[bold]Resume {escape(session_id)}")
    try:
        report = harness.resume_task(settings, session_id, accept_working_copy=accept_working_copy,
                                     on_event=_print_event)  # fmt: skip
    except harness.ResumeRefused as exc:
        console.print("[bold yellow]PAUSED[/bold yellow]: the session was not resumed, nothing was executed.")
        for reason in exc.reasons:
            console.print(f"  - {escape(reason)}")
        raise typer.Exit(1) from None
    except (SessionError, WorkspaceError) as exc:
        _fail(str(exc))
    _show_report(report)
    raise typer.Exit(0 if report.verified else 1)


@app.command()
def show(session_id: SessionArg, config: ConfigOption = Path("harness.toml")) -> None:
    """Show a session's saved result: changed files, diff, checks and unfinished work."""
    try:
        report = harness.load_report(_load(config), session_id)
    except SessionError as exc:
        _fail(str(exc))
    _show_report(report)


def _session(settings: HarnessConfig, session_id: str):
    try:
        return SessionStore(settings.workspace_dir).open(session_id)
    except SessionError as exc:
        _fail(str(exc))


def _status_text(status: SessionStatus) -> str:
    style = {SessionStatus.FINISHED: "green", SessionStatus.RUNNING: "blue"}.get(status, "yellow")
    return f"[{style}]{status}[/{style}]"


@app.command()
def projects(config: ConfigOption = Path("harness.toml")) -> None:
    """List the projects a task may select (\\[projects] in the settings)."""
    for name, project in _load(config).projects.items():
        checks = ", ".join(project.checks) or "none"
        console.print(f"[bold]{escape(name)}[/bold]  {escape(str(project.source))} @ {project.commit}"
                      f"  [dim]checks: {escape(checks)}[/dim]")  # fmt: skip


def _task(text: str | None, task_file: Path | None, allow: list[str] | None, project: str | None) -> TaskSpec:
    if (text is None) == (task_file is None):
        raise typer.BadParameter("give exactly one of --task or --task-file")
    if task_file is not None:
        try:
            spec = load_task(task_file)
        except ConfigError as exc:
            _fail(str(exc))
        return spec.model_copy(update={"project": project}) if project else spec
    if not text.strip():
        raise typer.BadParameter("the task is empty")
    if len(text.split()) == 1:
        # A one-word task is almost always a task name or file name typed into --task.
        candidate = Path("tasks") / f"{Path(text).stem}.toml"
        hint = f"; did you mean --task-file {candidate}?" if candidate.is_file() else ""
        raise typer.BadParameter(f"{text!r} is not a task description (one word){hint}")
    if not allow:
        raise typer.BadParameter("--task needs at least one --allow glob: the files the agent may change")
    return TaskSpec(project=project, request=text, allowed_paths=allow)


def _project(settings: HarnessConfig, spec: TaskSpec):
    try:
        return settings.project(spec.project)
    except ConfigError as exc:
        _fail(str(exc))


def _load(path: Path) -> HarnessConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        _fail(str(exc))


def _fail(message: str) -> None:
    console.print(f"[bold red]error:[/bold red] {escape(message)}")
    raise typer.Exit(2)


def _print_event(event: Event) -> None:
    d = event.data
    match event.kind:
        case "run_started":
            console.print(f"[bold]session {d['session']}[/bold] [dim](Ctrl+C cancels; "
                          f"`coding-harness resume {d['session']}` continues)[/dim]")  # fmt: skip
        case "interrupted_action":
            console.print(f"[yellow]interrupted, not replayed:[/yellow] {escape(d['name'])}")
        case "working_copy_accepted":
            console.print("[yellow]working copy differs from the checkpoint; accepted by the user[/yellow]")
        case "cancelled":
            console.print("[bold yellow]cancelled[/bold yellow]: no further actions, final checks skipped")
        case "model_request":
            retry = f" (attempt {d['attempt']})" if d["attempt"] > 1 else ""
            console.print(f"[dim]step {event.step}: asking the model{retry}…[/dim]")
        case "model_reply" if d["content"].strip() and not d["tool_calls"]:
            console.print(Panel(escape(d["content"].strip()), title="model", border_style="blue"))
        case "model_reply" if d["content"].strip():
            console.print(f"[blue]model:[/blue] {escape(_one_line(d['content'], 160))}")
        case "model_error":
            console.print(f"[yellow]model error:[/yellow] {escape(d['error'])}")
        case "tool_call":
            args = json.dumps(d["arguments"], ensure_ascii=False, default=str)
            console.print(f"[cyan]→ {escape(d['name'])}[/cyan] {escape(_one_line(args, 160))}")
        case "tool_result":
            style = {"ok": "green", "error": "red", "denied": "magenta"}[d["status"]]
            note = " [dim](shortened)[/dim]" if d["shortened"] else ""
            console.print(f"  [{style}]{d['status']}[/{style}]{note} {escape(_one_line(d['content'], 140))}")
        case "limit":
            console.print(f"[bold red]limit reached:[/bold red] {d['limit']} = {d['value']}")
        case "stop":
            console.print(f"[bold]agent stopped:[/bold] {d['reason']}")
        case "review":
            console.rule(f"[bold]Review, round {d['round']} (advisory)")
        case "review_done":
            style = {"approved": "green", "changes requested": "yellow"}.get(d["status"], "red")
            console.print(f"[{style}]reviewer: {d['status']}[/{style}]"
                          + (f" [dim]({escape(d['error'])})[/dim]" if d["error"] else ""))  # fmt: skip
            for finding in d["findings"]:
                console.print(f"  - {escape(finding)}")
        case "agent_checks":
            console.rule(f"[bold]Agent checks after proposed completion, round {d['round']}")
        case "agent_checks_done":
            for name, status in d["checks"].items():
                style = STATUS_STYLE[CheckStatus(status)]
                console.print(f"  {escape(name)}: [{style}]{status}[/{style}]")
        case "verification":
            console.rule("[bold]Final checks (independent of the model)")


def _show_report(report: harness.RunReport) -> None:
    outcome, verification = report.outcome, report.verification
    if outcome is not None:
        stats = outcome.stats
        claim = "model proposed completion" if outcome.stop_reason is StopReason.COMPLETED else "no completion"
        console.print(Panel(
            escape(outcome.final_message.strip() or "(no message)"),
            title=f"Agent result: {outcome.stop_reason} ({claim})",
            subtitle=(f"model calls {stats.model_calls} · actions {stats.actions} · executed {stats.executed}"
                      f" · denied {stats.denied} · failed {stats.failed} · retries {stats.retries}"
                      f" · fix rounds {stats.fix_rounds}"
                      f" · tokens {stats.prompt_tokens} in / {stats.completion_tokens} out"
                      f" · largest prompt {stats.max_prompt_tokens}"),
        ))  # fmt: skip

    console.rule("[bold]Changed files")
    if verification.changed_files:
        for status, path in verification.changed_files:
            console.print(f"  {status}  {escape(path)}")
        console.rule("[bold]Diff")
        console.print(Syntax(verification.diff, "diff", word_wrap=True))
    else:
        console.print("  (no changes)")

    console.rule("[bold]Checks")
    table = Table(show_header=True, header_style="bold")
    for column in ("check", "kind", "command", "exit", "time", "status"):
        table.add_column(column)
    for check in verification.checks:
        r, style = check.result, STATUS_STYLE[check.status]
        exit_text = "timeout" if r.timed_out else ("-" if r.exit_code is None else str(r.exit_code))
        table.add_row(check.name, str(check.kind), escape(" ".join(r.command)), exit_text, f"{r.duration:.1f}s",
                      f"[{style}]{check.status}[/{style}]")  # fmt: skip
    if report.status is SessionStatus.CANCELLED:
        console.print("  (skipped: the session was cancelled)")
    else:
        console.print(table if verification.checks else "  (no checks configured)")
    for check in verification.checks:
        if check.status is not CheckStatus.PASSED:
            body = check.result.error or _tail(check.result.output, 40)
            console.print(Panel(escape(body), title=f"{check.name}: {check.status}", border_style="red"))

    if report.reviews:
        last = report.reviews[-1]
        console.print(f"review (advisory, not part of verification): {last.status} after "
                      f"{len(report.reviews)} review(s)"
                      + (f", {len(last.findings)} open finding(s)" if last.findings else ""))  # fmt: skip
    if unfinished := _unfinished(report):
        console.print(Panel("\n".join(f"- {escape(item)}" for item in unfinished), title="Unfinished work",
                            border_style="yellow"))  # fmt: skip
    if report.verified:
        console.print("[bold green]VERIFIED[/bold green]: the acceptance check and all other final checks passed.")
    elif report.status is SessionStatus.CANCELLED:
        console.print("[bold yellow]NOT VERIFIED[/bold yellow]: the session was cancelled before the final checks.")
    elif not verification.has_acceptance:
        console.print("[bold yellow]NOT VERIFIED[/bold yellow]: the task has no acceptance check "
                      "(\\[acceptance] in the task file), so nothing shows the task was solved.")
    else:
        console.print("[bold red]NOT VERIFIED[/bold red]: at least one final check failed or was unavailable.")  # fmt: skip
    console.print(f"[dim]workspace: {report.workspace.repo}\ntrace: {report.trace_path}[/dim]")


def _unfinished(report: harness.RunReport) -> list[str]:
    """What is left open: shown next to the diff so it is not mistaken for finished work."""
    items = []
    if report.status is SessionStatus.CANCELLED:
        items.append(f"cancelled: the final checks did not run; continue with `coding-harness resume {report.session_id}`")
    outcome = report.outcome
    if outcome is not None and outcome.stop_reason not in (StopReason.COMPLETED, StopReason.INTERRUPTED):
        items.append(f"the agent did not complete the task: stopped with {outcome.stop_reason}")
    for check in report.verification.checks:
        if check.status is not CheckStatus.PASSED:
            items.append(f"{check.kind} check {check.name!r} {check.status}")
    for action in report.uncertain:
        items.append(f"interrupted action, outcome unknown, not replayed: {action['name']} "
                     f"{_one_line(json.dumps(action.get('arguments'), default=str), 100)}")  # fmt: skip
    return items


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])
