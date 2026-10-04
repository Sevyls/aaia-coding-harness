"""The lint check counts only problems a change introduces, during the run and at the end."""

import json
from pathlib import Path

from coding_harness import harness
from coding_harness.config import HarnessConfig
from coding_harness.domain import ToolCall
from coding_harness.lint import new_problems
from coding_harness.toolset import build_toolset
from coding_harness.model_client import ScriptedModelClient
from coding_harness.tools import RepositoryTools
from coding_harness.verification import CheckKind, CheckStatus

from helpers import FakeEnvironment, call, done

HANDLERS = "import os\n\n\nclass InvalidSku(Exception):\n    pass\n\n\ndef path():\n    return os.sep\n"


def test_code_between_imports_is_a_new_problem():
    # qwen2.5-coder:7b put the new exception class between the imports
    after = "class InvalidQuantity(Exception):\n    pass\n" + HANDLERS
    assert [(p.code, p.line) for p in new_problems(HANDLERS, after)] == [("E402", 3)]


def test_problems_that_existed_before_do_not_count():
    before = "import os\nimport sys\n\n\ndef f():\n    return 1\n"  # two unused imports
    after = before.replace("return 1", "return 2")
    assert new_problems(before, after) == []
    assert [p.code for p in new_problems(before, "import json\n" + after)] == ["UnusedImport"]


def test_edit_that_introduces_a_lint_problem_is_kept_with_a_warning(repo):
    (repo / "handlers.py").write_text(HANDLERS)
    message = RepositoryTools(repo).edit_file("handlers.py", "import os\n", "import os\nimport json\n")
    assert "1 new lint problem(s)" in message
    assert "UnusedImport 'json' imported but unused" in message
    assert "import json" in (repo / "handlers.py").read_text()


def test_lint_warning_compares_with_the_start_of_the_run_not_the_last_edit(repo):
    # qwen2.5-coder:7b made a lint problem worse; a later edit must still report it
    (repo / "handlers.py").write_text(HANDLERS)
    tools = RepositoryTools(repo)
    tools.edit_file("handlers.py", "import os\n", "import os\nimport json\n")
    message = tools.edit_file("handlers.py", "return os.sep", "return os.sep * 2")
    assert "UnusedImport 'json'" in message
    assert "fixed" in tools.edit_file("handlers.py", "import json\n", "")


def test_agent_can_run_the_lint_check(repo):
    (repo / "handlers.py").write_text(HANDLERS)
    tools = RepositoryTools(repo)
    registry = build_toolset(tools, FakeEnvironment(), {"unit": ["pytest"]}, lint=True)
    tools.edit_file("handlers.py", "import os\n", "import os\nimport json\n")
    run_check, args = registry.validate(ToolCall("run_check", {"name": "lint"}))
    assert "lint = static check" in run_check.description
    assert "handlers.py:line 2: UnusedImport" in run_check.handler(args)


def test_lint_is_not_offered_to_the_agent_when_disabled(repo):
    registry = build_toolset(RepositoryTools(repo), FakeEnvironment(), {"unit": ["pytest"]})
    assert "lint" not in json.dumps(registry.specs())


def test_clean_edit_has_no_lint_warning(repo):
    (repo / "handlers.py").write_text(HANDLERS)
    message = RepositoryTools(repo).edit_file("handlers.py", "return os.sep", "return os.sep * 2")
    assert "warning" not in message


def make_config(source: Path, tmp_path: Path) -> HarnessConfig:
    return HarnessConfig.model_validate({
        "workspace_dir": tmp_path / "runs",
        "target": {"source": source, "commit": "HEAD"},
        "sandbox": {"image": "unused"},
        "verification": {"acceptance": {"acceptance": ["pytest", "/acceptance"]}, "lint": True},
    })  # fmt: skip


def run(git_repo, tmp_path, *replies):
    return harness.run_task(make_config(git_repo, tmp_path), "fix total",
                            model=ScriptedModelClient([*replies, done()]),
                            env_factory=lambda repo, mounts: FakeEnvironment())  # fmt: skip


def test_final_lint_check_fails_on_a_new_problem_and_blocks_verified(git_repo, tmp_path):
    report = run(git_repo, tmp_path, call("edit_file", path="shop/pricing.py", old_text="def total",
                                          new_text="import os\n\n\ndef total"))  # fmt: skip
    lint = report.verification.checks[-1]
    assert (lint.name, lint.kind, lint.status) == ("lint", CheckKind.QUALITY, CheckStatus.FAILED)
    assert "shop/pricing.py:line 1: UnusedImport 'os' imported but unused" in lint.result.output
    assert not report.verified  # the acceptance check passed, the lint check did not


def test_final_lint_check_passes_on_a_clean_change(git_repo, tmp_path):
    report = run(git_repo, tmp_path, call("edit_file", path="shop/pricing.py", old_text=" - 1  # bug",
                                          new_text=""))  # fmt: skip
    assert report.verification.checks[-1].status is CheckStatus.PASSED
    assert report.verified
