"""Commands keep exit codes and output visible, are bounded, and can be stopped completely."""

import sys
import time

from coding_harness.process import run_bounded


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_nonzero_exit_code_and_output_stay_visible():
    result = run_bounded(python("print('1 failed'); raise SystemExit(3)"), timeout=10, output_limit=1000)
    assert result.exit_code == 3
    assert "1 failed" in result.output
    assert not result.passed
    assert "exit code: 3" in result.describe()


def test_large_output_is_bounded_and_marked():
    result = run_bounded(python("print('x' * 200_000); print('SUMMARY')"), timeout=10, output_limit=1000)
    assert result.truncated
    assert "[... output shortened by the harness:" in result.output
    assert len(result.output) < 1100
    assert result.output.rstrip().endswith("SUMMARY")  # the tail survives


def test_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / "late-write.txt"
    child = tmp_path / "child.py"
    child.write_text(f"import time\ntime.sleep(1.5)\nopen({str(marker)!r}, 'w').write('x')\n")
    # The parent starts a child that would write a file later, then hangs.
    code = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {str(child)!r}])\ntime.sleep(60)\n"
    stopped = []

    result = run_bounded(python(code), timeout=0.5, output_limit=1000, on_timeout=lambda: stopped.append(True))

    assert result.timed_out and not result.passed
    assert stopped == [True]
    time.sleep(2.0)
    assert not marker.exists(), "the child process kept running and wrote after the stop"


def test_missing_executable_is_reported_not_raised():
    result = run_bounded(["definitely-not-a-command-xyz"], timeout=5, output_limit=1000)
    assert result.exit_code is None
    assert "could not start" in result.error
    assert not result.passed
