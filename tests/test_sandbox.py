"""The container command is locked down, and a timed-out container is removed."""

from pathlib import Path

from coding_harness.config import SandboxConfig
from coding_harness.domain import CommandResult
from coding_harness.sandbox import ContainerSandbox, Mount


def make_sandbox(runner=None, **overrides):
    config = SandboxConfig(image="target:latest", env={"PYTHONPATH": "/work/src"}, **overrides)
    mounts = [Mount(Path("/checks"), "/acceptance")]
    return ContainerSandbox(config, Path("/runs/1/repo"), mounts, **({"runner": runner} if runner else {}))


def test_container_argv_is_locked_down():
    argv = make_sandbox().container_argv(["pytest", "-q"], "harness-abc")

    joined = " ".join(argv)
    assert argv[:5] == ["podman", "run", "--rm", "--name", "harness-abc"]
    for flag in ("--network none", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--memory 1g", "--pids-limit 256", "--read-only"):  # fmt: skip
        assert flag in joined
    assert "--volume /runs/1/repo:/work:ro" in joined
    assert "--volume /checks:/acceptance:ro" in joined
    assert "--env PYTHONPATH=/work/src" in joined
    assert argv[-3:] == ["target:latest", "pytest", "-q"]


def test_only_the_repository_and_explicit_mounts_are_visible():
    argv = make_sandbox().container_argv(["true"], "n")
    volumes = [argv[i + 1] for i, a in enumerate(argv) if a == "--volume"]
    assert volumes == ["/runs/1/repo:/work:ro", "/checks:/acceptance:ro"]


def test_run_reports_the_inner_command_and_keeps_the_full_argv():
    def runner(argv, **_):
        return CommandResult(command=argv, exit_code=1, output="1 failed")

    result = make_sandbox(runner).run(["pytest", "-q"])

    assert result.command == ["pytest", "-q"]
    assert result.executed_argv[0] == "podman"
    assert result.exit_code == 1 and result.output == "1 failed"


def test_timeout_removes_the_container():
    def runner(argv, *, on_timeout, **_):
        on_timeout()
        return CommandResult(command=argv, exit_code=-9, output="", timed_out=True)

    sandbox = make_sandbox(runner)
    removed = []
    sandbox.stop = removed.append

    result = sandbox.run(["sleep", "999"])

    assert result.timed_out
    assert len(removed) == 1 and removed[0].startswith("harness-")
