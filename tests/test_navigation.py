"""Navigation finds definitions, uses and imports, reports git state, and stays bounded."""

import pytest

from coding_harness.navigation import Navigator
from coding_harness.tools import RepositoryTools
from coding_harness.workspace import _git


def navigator(root, **limits):
    return Navigator(RepositoryTools(root, **limits), lambda args: _git(args, cwd=root))


@pytest.fixture
def nav(repo):
    return navigator(repo)


def test_find_symbol_lists_definitions(nav, repo):
    (repo / "shop" / "models.py").write_text("RATE = 3\n\n\nclass Cart:\n    def total(self):\n        pass\n")
    found = nav.find_symbol("total").splitlines()
    assert found == ["shop/models.py:5: def total(self):", "shop/pricing.py:1: def total(prices):"]
    assert nav.find_symbol("RATE") == "shop/models.py:1: RATE = 3"
    assert "no definition found" in nav.find_symbol("missing")


def test_find_references_lists_uses(nav):
    found = nav.find_references("total").splitlines()
    assert found == ["shop/cart.py:1: from shop.pricing import total", "shop/cart.py:5: return total(cart)"]
    assert "no references found" in nav.find_references("missing")


def test_find_references_is_bounded(repo):
    (repo / "many.py").write_text("x = 1\n" * 10)
    found = navigator(repo, max_search_matches=3).find_references("x")
    assert found.splitlines()[-1] == "... stopped after 3 matches"


def test_module_deps_both_directions(nav):
    deps = nav.module_deps("shop/cart.py")
    assert "shop.pricing -> shop/pricing.py" in deps
    pricing = nav.module_deps("shop/pricing.py")
    assert pricing.split("imported by:")[1].split() == ["shop/cart.py"]


def test_module_deps_src_layout_and_relative_imports(repo):
    package = repo / "src" / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "core.py").write_text("import os\nfrom . import util\nfrom .util import helper\n")
    (package / "util.py").write_text("def helper():\n    pass\n")
    (repo / "main.py").write_text("from app.core import os\n")
    deps = navigator(repo).module_deps("src/app/core.py")
    assert "os -> not in the repository" in deps
    assert ".util -> src/app/util.py" in deps
    assert "main.py" in deps.split("imported by:")[1]


def test_git_status_clean_then_modified(git_repo):
    nav = navigator(git_repo)
    start = _git(["rev-parse", "HEAD"], cwd=git_repo).strip()
    assert "(no uncommitted changes)" in nav.git_status(start)
    (git_repo / "shop" / "pricing.py").write_text("def total(prices):\n    return sum(prices)\n")
    status = nav.git_status(start)
    assert "HEAD is the start commit" in status
    assert "M shop/pricing.py" in status and "1 file changed" in status


def test_repo_map_lists_classes_and_functions(nav, repo):
    (repo / "shop" / "models.py").write_text("class Cart:\n    def add(self):\n        pass\n")
    lines = nav.repo_map(10_000).splitlines()
    assert "shop/cart.py: def checkout" in lines
    assert "shop/models.py: class Cart(add)" in lines


def test_repo_map_respects_max_chars(nav, repo):
    for i in range(50):
        (repo / f"module_{i}.py").write_text(f"def function_{i}():\n    pass\n")
    shown = nav.repo_map(500)
    assert len(shown) <= 500 and "output shortened" in shown


def test_syntax_error_is_reported_not_raised(nav, repo):
    (repo / "broken.py").write_text("def total(:\n")
    assert "broken.py: could not be parsed (line 1" in nav.find_symbol("total")
    assert "broken.py: could not be parsed" in nav.find_references("total")
    assert "broken.py: could not be parsed" in nav.repo_map(10_000)
    assert "broken.py: could not be parsed" in nav.module_deps("shop/cart.py")
