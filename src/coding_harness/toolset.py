"""Tool descriptions for the model, argument schemas and validation of tool requests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from coding_harness.domain import ToolCall
from coding_harness.sandbox import ExecutionEnvironment
from coding_harness.tools import RepositoryTools

FINISH = "finish"


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(Args):
    path: str = Field(".", description="Directory relative to the repository root")


class ReadFileArgs(Args):
    path: str = Field(description="File path relative to the repository root")
    start_line: int = Field(1, ge=1, description="First line to read (1-based)")
    end_line: int | None = Field(None, ge=1, description="Last line to read; omit to read to the end")


class SearchArgs(Args):
    pattern: str = Field(min_length=1, description="Text to find in file contents")
    path: str = Field(".", description="File or directory to search, relative to the repository root")
    regex: bool = Field(False, description="Treat pattern as a Python regular expression")


class EditFileArgs(Args):
    path: str = Field(description="File path relative to the repository root")
    old_text: str = Field(min_length=1, description="Exact text to replace; must occur exactly once")
    new_text: str = Field(description="Replacement text")


class WriteFileArgs(Args):
    path: str = Field(description="File path relative to the repository root")
    content: str = Field(description="Complete new file content")


class FinishArgs(Args):
    summary: str = Field(min_length=1, description="What was changed and why")


class ActionRejected(Exception):
    """A tool request was refused before execution (unknown tool or invalid arguments)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any], str]
    writes: bool = False  # changes the repository; used by loop detection

    def spec(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool], state: Callable[[], object] = lambda: None) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self.state = state  # changes whenever the repository changes; used to detect loops

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def writes(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.writes

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def validate(self, call: ToolCall) -> tuple[Tool, BaseModel]:
        """Check the tool name and the argument shape. Raises ActionRejected."""
        tool = self._tools.get(call.name)
        if tool is None:
            raise ActionRejected(
                f"unknown tool {call.name!r}; available tools: {', '.join(self._tools)}"
            )
        if not isinstance(call.arguments, Mapping):
            raise ActionRejected(f"arguments for {call.name!r} must be a JSON object")
        try:
            args = tool.args_model.model_validate(dict(call.arguments))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
                for error in exc.errors()
            )
            raise ActionRejected(f"invalid arguments for {call.name!r}: {problems}") from None
        return tool, args


LINT_CHECK = "lint"


def check_names(checks: Mapping[str, list[str]], lint: bool) -> list[str]:
    """Names the agent may pass to run_check: configured commands, plus the built-in lint."""
    return [*checks, LINT_CHECK] if lint and LINT_CHECK not in checks else list(checks)


def build_toolset(
    repo: RepositoryTools, env: ExecutionEnvironment, checks: Mapping[str, list[str]], *, lint: bool = False
) -> ToolRegistry:
    tools = [
        Tool(
            "list_files",
            "List files in a directory of the repository, recursively.",
            ListFilesArgs,
            lambda a: repo.list_files(a.path),
        ),
        Tool(
            "read_file",
            "Read a text file, optionally a range of lines.",
            ReadFileArgs,
            lambda a: repo.read_file(a.path, a.start_line, a.end_line),
        ),
        Tool(
            "search",
            "Search file contents. Returns path:line: text for each match.",
            SearchArgs,
            lambda a: repo.search(a.pattern, a.path, a.regex),
        ),
        Tool(
            "edit_file",
            "Replace one exact, unique occurrence of old_text with new_text in a file.",
            EditFileArgs,
            lambda a: repo.edit_file(a.path, a.old_text, a.new_text),
            writes=True,
        ),
        Tool(
            "write_file",
            "Create a file or replace its whole content.",
            WriteFileArgs,
            lambda a: repo.write_file(a.path, a.content),
            writes=True,
        ),
    ]
    names = check_names(checks, lint)
    if names:
        # Only configured commands (and the built-in lint) can run; the schema lists the names.
        run_check_args = create_model(
            "RunCheckArgs",
            __base__=Args,
            name=(
                Literal[tuple(names)],
                Field(description="Name of a configured check", json_schema_extra={"enum": names}),
            ),
        )
        available = [f"{name} = {' '.join(cmd)}" for name, cmd in checks.items()]
        if LINT_CHECK in names and LINT_CHECK not in checks:
            available.append(f"{LINT_CHECK} = static check of the Python files you changed (new pyflakes "
                             "problems, code before imports)")  # fmt: skip

        def run_check(a: BaseModel) -> str:
            if a.name not in checks:  # the built-in lint: static, in the harness, never runs code
                return repo.lint()
            return env.run(list(checks[a.name])).describe()

        tools.append(
            Tool("run_check", "Run a check. Available: " + "; ".join(available), run_check_args, run_check)
        )
    tools.append(
        Tool(
            FINISH,
            "Propose that the task is complete. The harness then runs the final checks.",
            FinishArgs,
            lambda a: "completion proposed; the harness will now run the final checks",
        )
    )
    return ToolRegistry(tools, state=repo.state)
