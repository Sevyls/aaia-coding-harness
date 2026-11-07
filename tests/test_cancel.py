"""Cancellation: during a model request or a command. Nothing runs afterwards, nothing is left behind."""

import _thread
import os
import sys
import threading
import time

import pytest

from coding_harness import harness
from coding_harness.domain import StopReason
from coding_harness.model_client import ScriptedModelClient
from coding_harness.process import run_bounded
from coding_harness.session import SessionStatus, SessionStore

from helpers import FakeEnvironment, call, make_config, make_task

FIX = call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug", new_text="")


class RecordingFactory:
    """Hands out fake environments and remembers them, to show which ones ran commands."""

    def __init__(self, *envs):
        self.envs, self.handed_out = list(envs), []

    def __call__(self, repo, mounts):
        env = self.envs.pop(0) if self.envs else FakeEnvironment()
        self.handed_out.append(env)
        return env


class InterruptedCommand(FakeEnvironment):
    def run(self, command, *, timeout=None):
        self.commands.append(list(command))
        raise KeyboardInterrupt  # Ctrl+C while the command runs


def test_cancel_during_a_model_request_stops_all_further_actions(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    model = ScriptedModelClient([FIX, KeyboardInterrupt(), call("write_file", path="shop/x.py", content="")])
    factory = RecordingFactory()

    report = harness.run_task(config, make_task(), model=model, env_factory=factory)

    assert report.status is SessionStatus.CANCELLED and report.outcome.stop_reason is StopReason.INTERRUPTED
    assert len(model.requests) == 2  # no request after the cancelled one
    assert not (report.workspace.repo / "shop" / "x.py").exists()
    assert len(factory.handed_out) == 1 and factory.handed_out[0].commands == []  # no final checks ran
    assert report.verification.checks == [] and not report.verified
    assert report.verification.changed_files == [("M", "shop/pricing.py")]  # shown, not checked

    session = SessionStore(config.workspace_dir).open(report.session_id)
    assert session.checkpoint().status is SessionStatus.CANCELLED
    assert session.running_pid() is None and not (session.dir / "pid").exists()
    before = report.workspace.fingerprint()
    time.sleep(0.2)
    assert report.workspace.fingerprint() == before  # no late writes


def test_cancel_during_a_command_marks_it_as_interrupted(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    model = ScriptedModelClient([call("run_check", name="tests")])

    report = harness.run_task(config, make_task(), model=model, env_factory=RecordingFactory(InterruptedCommand()))

    assert report.status is SessionStatus.CANCELLED
    assert [a["name"] for a in report.uncertain] == ["run_check"]  # started, no result: outcome unknown
    session = SessionStore(config.workspace_dir).open(report.session_id)
    assert [a["op"] for a in session.uncertain_actions()] == ["1.1"]


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows kills the process tree")
def test_interrupt_during_a_command_kills_its_child_processes(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = ("import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(60)\n")  # fmt: skip
    threading.Timer(1.0, _thread.interrupt_main).start()  # Ctrl+C arrives while the command runs

    with pytest.raises(KeyboardInterrupt):
        run_bounded([sys.executable, "-c", code], timeout=30, output_limit=1000)

    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)  # the grandchild is gone too, not only the direct child
