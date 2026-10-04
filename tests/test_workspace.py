"""The agent works on a disposable clone; the source repository is never touched."""

import subprocess

import pytest

from coding_harness.workspace import Workspace, WorkspaceError


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def test_clone_is_disposable_and_has_no_remote(git_repo, tmp_path):
    head = git(git_repo, "rev-parse", "HEAD").strip()

    ws = Workspace.create(git_repo, head, tmp_path / "runs", run_id="r1")
    (ws.repo / "shop" / "pricing.py").write_text("changed\n")

    assert ws.commit == head
    assert ws.repo == tmp_path / "runs" / "r1" / "repo"
    assert git(ws.repo, "remote").strip() == ""  # push has nowhere to go
    assert "sum(prices) - 1" in (git_repo / "shop" / "pricing.py").read_text()  # source untouched


def test_diff_and_changed_files_include_new_files(git_repo, tmp_path):
    ws = Workspace.create(git_repo, "HEAD", tmp_path / "runs")
    (ws.repo / "shop" / "pricing.py").write_text("def total(prices):\n    return sum(prices)\n")
    (ws.repo / "shop" / "tax.py").write_text("RATE = 20\n")

    assert ws.changed_files() == [("M", "shop/pricing.py"), ("A", "shop/tax.py")]
    diff = ws.diff()
    assert "-    return sum(prices) - 1  # bug" in diff
    assert "+RATE = 20" in diff


def test_clone_keeps_lf_line_endings_even_with_autocrlf(git_repo, tmp_path, monkeypatch):
    # Git for Windows sets core.autocrlf=true; CRLF files would make every LF edit miss.
    config = tmp_path / "gitconfig"
    config.write_text("[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    ws = Workspace.create(git_repo, "HEAD", tmp_path / "runs")

    assert b"\r\n" not in (ws.repo / "shop" / "pricing.py").read_bytes()
    assert ws.changed_files() == []


def test_unknown_commit_is_a_clear_error(git_repo, tmp_path):
    with pytest.raises(WorkspaceError, match="checkout"):
        Workspace.create(git_repo, "0" * 40, tmp_path / "runs")
