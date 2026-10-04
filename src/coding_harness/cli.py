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
from coding_harness.config import ConfigError, HarnessConfig, load_config
from coding_harness.domain import Event, StopReason
from coding_harness.verification import CheckStatus
from coding_harness.workspace import WorkspaceError

app = typer.Typer(help="A small coding harness: a task goes in, a reviewable diff comes out.",
                  no_args_is_help=True, add_completion=False)  # fmt: skip
console = Console()

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Harness settings (TOML)")]
STATUS_STYLE = {CheckStatus.PASSED: "green", CheckStatus.FAILED: "red", CheckStatus.UNAVAILABLE: "yellow"}


@app.command()
def run(
    task: Annotated[str | None, typer.Option("--task", "-t", help="The coding task")] = None,
    task_file: Annotated[Path | None, typer.Option(help="Read the task from a file")] = None,
    config: ConfigOption = Path("harness.toml"),
    model: Annotated[str | None, typer.Option(help="Override the model name")] = None,
    review: Annotated[bool | None, typer.Option("--review/--no-review", help="Override [review] enabled")] = None,
    review_model: Annotated[str | None, typer.Option(help="Reviewer model name (turns review on)")] = None,
) -> None:
    """Run the agent on a task in a fresh copy of the target repository."""
    if (task is None) == (task_file is None):
        raise typer.BadParameter("give exactly one of --task or --task-file")
    if task is not None and len(task.split()) == 1:
        # A one-word task is almost always a task name or file name typed into --task.
        candidate = Path("tasks") / f"{Path(task).stem}.md"
        hint = f"; did you mean --task-file {candidate}?" if candidate.is_file() else ""
        raise typer.BadParameter(f"{task!r} is not a task description (one word){hint}")
    text = task if task is not None else task_file.read_text(encoding="utf-8")
    if not text.strip():
        raise typer.BadParameter("the task is empty")
    settings = _load(config)
    if model:
        settings.model.name = model
    if review_model:
        settings.review.enabled = True
        base = settings.review.model or settings.model
        settings.review.model = base.model_copy(update={"name": review_model})
    if review is not None:
        settings.review.enabled = review

    console.rule("[bold]Task")
    console.print(escape(text.strip()))
    reviewer = (settings.review.model or settings.model).name if settings.review.enabled else "off"
    console.print(f"[dim]model {settings.model.name} · reviewer {reviewer} · target {settings.target.source.name}"
                  f" @ {settings.target.commit}[/dim]")  # fmt: skip
    try:
        report = harness.run_task(settings, text, on_event=_print_event)
    except WorkspaceError as exc:
        _fail(str(exc))
    _show_report(report)
    raise typer.Exit(0 if report.verified else 1)


@app.command()
def baseline(config: ConfigOption = Path("harness.toml")) -> None:
    """Run the final checks on the untouched starting commit (the acceptance check should fail)."""
    settings = _load(config)
    try:
        report = harness.run_baseline(settings)
    except WorkspaceError as exc:
        _fail(str(exc))
    _show_report(report)


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
    if report.verified:
        console.print("[bold green]VERIFIED[/bold green]: the acceptance check and all other final checks passed.")
    elif not verification.has_acceptance:
        console.print("[bold yellow]NOT VERIFIED[/bold yellow]: no acceptance check is configured "
                      "(\\[verification.acceptance] in harness.toml), so nothing shows the task was solved.")
    else:
        console.print("[bold red]NOT VERIFIED[/bold red]: at least one final check failed or was unavailable.")  # fmt: skip
    console.print(f"[dim]workspace: {report.workspace.repo}\ntrace: {report.trace_path}[/dim]")


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])
