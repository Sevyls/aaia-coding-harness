"""Harness settings, loaded from a TOML file and validated with Pydantic."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# A command is an argv list. It never passes through a shell.
Command = Annotated[list[str], Field(min_length=1)]


class ConfigError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelConfig(_Section):
    provider: Literal["ollama"] = "ollama"
    name: str = "qwen2.5-coder:3b"
    host: str = "http://localhost:11434"
    temperature: float = 0.0
    request_timeout: float = Field(600, gt=0)
    think: bool | None = None  # None leaves the model's default
    context_window: int | None = Field(None, ge=512)  # Ollama num_ctx; None uses the server default


class TargetConfig(_Section):
    source: Path  # the target repository; the agent only ever sees a disposable clone
    commit: str
    scope: str = ""  # which files or behaviour the task may change, shown to the model
    # Glob patterns (relative to the repository root, ** allowed) for the files the agent may
    # write. Enforced by the file tools and again by a final check. Empty: no restriction.
    allowed_paths: list[str] = Field(default_factory=list)


class LimitsConfig(_Section):
    max_steps: int = Field(30, ge=1)  # model requests
    max_actions: int = Field(40, ge=1)  # tool requests, executed or denied
    max_denied: int = Field(5, ge=1)
    max_repeated_actions: int = Field(4, ge=0)  # same request with the repo unchanged, or same write; whole run; 0 disables
    max_model_retries: int = Field(2, ge=0)
    tool_output_chars: int = Field(8000, ge=200)
    max_write_chars: int = Field(100_000, ge=1)
    max_list_entries: int = Field(400, ge=1)
    max_search_matches: int = Field(100, ge=1)


class SandboxConfig(_Section):
    engine: str = "podman"
    image: str
    network: str = "none"
    memory: str = "1g"
    cpus: float = Field(2, gt=0)
    pids_limit: int = Field(256, ge=1)
    workdir: str = "/work"
    repo_read_only: bool = True  # only the harness file tools write to the repository
    env: dict[str, str] = Field(default_factory=dict)
    command_timeout: float = Field(180, gt=0)
    output_limit: int = Field(20_000, ge=200)


class VerificationConfig(_Section):
    acceptance_dir: Path | None = None  # mounted read-only at /acceptance, never visible to the agent
    acceptance: dict[str, Command] = Field(default_factory=dict)
    regression: dict[str, Command] = Field(default_factory=dict)
    lint: bool = False  # final check: the change introduces no lint problems (static, in-process)


class ReviewConfig(_Section):
    enabled: bool = False
    model: ModelConfig | None = None  # None reviews with the same model as [model]
    max_rounds: int = Field(1, ge=0)  # how often review findings go back to the coding agent


class HarnessConfig(_Section):
    workspace_dir: Path = Path(".harness/runs")
    model: ModelConfig = Field(default_factory=ModelConfig)
    target: TargetConfig
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    sandbox: SandboxConfig
    checks: dict[str, Command] = Field(default_factory=dict)  # commands the agent may run by name
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)


def load_config(path: Path) -> HarnessConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"config file not found: {path} (copy harness.example.toml to harness.toml)"
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    try:
        config = HarnessConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid settings in {path}:\n{exc}") from None

    # Relative paths are relative to the config file, not the current directory.
    base = path.resolve().parent
    config.workspace_dir = base / config.workspace_dir
    config.target.source = (base / config.target.source).resolve()
    if not (config.target.source / ".git").exists():
        raise ConfigError(f"target.source is not a git repository: {config.target.source}")
    if config.verification.acceptance_dir is not None:
        acceptance_dir = (base / config.verification.acceptance_dir).resolve()
        if not acceptance_dir.is_dir():
            raise ConfigError(f"verification.acceptance_dir does not exist: {acceptance_dir}")
        config.verification.acceptance_dir = acceptance_dir
    return config
