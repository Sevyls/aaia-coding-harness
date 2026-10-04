"""Execution environment: configured commands run in a throwaway container."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from coding_harness.config import SandboxConfig
from coding_harness.domain import CommandResult
from coding_harness.process import run_bounded


class ExecutionEnvironment(Protocol):
    def run(self, command: list[str], *, timeout: float | None = None) -> CommandResult: ...


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool = True


class ContainerSandbox:
    """Runs each command in a new container that only sees the repository copy.

    No network, no capabilities, resource limits, read-only root filesystem, and no
    host files except the explicit mounts. Works with podman or docker.
    """

    def __init__(
        self,
        config: SandboxConfig,
        repo_dir: Path,
        extra_mounts: Sequence[Mount] = (),
        runner: Callable[..., CommandResult] = run_bounded,
    ) -> None:
        self.config = config
        self.mounts = [Mount(repo_dir, config.workdir, config.repo_read_only), *extra_mounts]
        self._runner = runner

    def container_argv(self, command: Sequence[str], name: str) -> list[str]:
        c = self.config
        argv = [
            c.engine, "run", "--rm", "--name", name,
            "--network", c.network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", c.memory,
            "--cpus", str(c.cpus),
            "--pids-limit", str(c.pids_limit),
            "--read-only", "--tmpfs", "/tmp",
            "--workdir", c.workdir,
        ]  # fmt: skip
        for mount in self.mounts:
            mode = "ro" if mount.read_only else "rw"
            argv += ["--volume", f"{mount.source.as_posix()}:{mount.target}:{mode}"]
        for key, value in sorted(c.env.items()):
            argv += ["--env", f"{key}={value}"]
        return [*argv, c.image, *command]

    def run(self, command: list[str], *, timeout: float | None = None) -> CommandResult:
        name = f"harness-{uuid.uuid4().hex[:12]}"
        argv = self.container_argv(command, name)
        try:
            result = self._runner(
                argv,
                timeout=timeout or self.config.command_timeout,
                output_limit=self.config.output_limit,
                on_timeout=lambda: self.stop(name),
            )
        except BaseException:
            # Killing the podman client does not stop the container; remove it explicitly.
            self.stop(name)
            raise
        return replace(result, command=list(command), executed_argv=argv)

    def stop(self, name: str) -> None:
        try:
            subprocess.run(
                [self.config.engine, "rm", "--force", name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
