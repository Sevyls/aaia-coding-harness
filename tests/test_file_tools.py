"""File tools read, search and edit the intended files and reject paths outside the repository."""

import pytest

from coding_harness.tools import PermissionDenied, RepositoryTools, ToolError


@pytest.fixture
def tools(repo):
    return RepositoryTools(repo)


def test_list_files_skips_caches(tools):
    listing = tools.list_files().splitlines()
    assert listing == ["README.md", "shop/__init__.py", "shop/cart.py", "shop/pricing.py"]


def test_empty_path_means_repository_root(tools):
    assert tools.list_files("") == tools.list_files(".")
    with pytest.raises(ToolError, match="file not found"):
        tools.read_file("")


def test_list_files_is_bounded(repo):
    listing = RepositoryTools(repo, max_list_entries=2).list_files()
    assert "2 more files not shown" in listing


def test_read_file_returns_content(tools):
    text = tools.read_file("shop/pricing.py")
    assert "return sum(prices) - 1" in text
    assert text.startswith("shop/pricing.py (lines 1-2 of 2)")


def test_read_file_line_range(tools):
    text = tools.read_file("shop/cart.py", start_line=4, end_line=4)
    assert text.splitlines()[1:] == ["def checkout(cart):"]


def test_read_missing_file_is_clear_error(tools):
    with pytest.raises(ToolError, match="file not found"):
        tools.read_file("shop/missing.py")


def test_search_finds_literal_text(tools):
    assert tools.search("total(").splitlines() == [
        "shop/cart.py:5:     return total(cart)",
        "shop/pricing.py:1: def total(prices):",
    ]
    assert tools.search("nothing like this") == "no matches for 'nothing like this'"


def test_search_regex_and_invalid_regex(tools):
    assert "shop/pricing.py:2:" in tools.search(r"sum\(\w+\)", regex=True)
    with pytest.raises(ToolError, match="invalid regular expression"):
        tools.search("(", regex=True)


def test_edit_file_replaces_unique_text(tools, repo):
    message = tools.edit_file("shop/pricing.py", "sum(prices) - 1  # bug", "sum(prices)")
    assert message.startswith("edited shop/pricing.py")
    assert (repo / "shop" / "pricing.py").read_text() == "def total(prices):\n    return sum(prices)\n"


def test_edit_file_requires_exact_unique_match(tools, repo):
    with pytest.raises(ToolError, match="not found"):
        tools.edit_file("shop/pricing.py", "sum(prices) + 1", "x")
    (repo / "twice.py").write_text("a = 1\na = 1\n")
    with pytest.raises(ToolError, match="occurs 2 times"):
        tools.edit_file("twice.py", "a = 1", "a = 2")


def test_write_file_creates_file_inside_repo(tools, repo):
    assert tools.write_file("shop/tax.py", "RATE = 20\n") == "created shop/tax.py (10 characters)"
    assert (repo / "shop" / "tax.py").read_text() == "RATE = 20\n"


def test_write_file_size_limit(repo):
    with pytest.raises(PermissionDenied, match="exceeds the limit"):
        RepositoryTools(repo, max_write_chars=5).write_file("big.txt", "123456")
    assert not (repo / "big.txt").exists()


@pytest.mark.parametrize(
    "path", ["../secret.txt", "shop/../../secret.txt", "/etc/passwd", ".git/config", "a\0b"]
)
def test_paths_outside_the_allowed_area_are_rejected(tools, repo, path):
    for action in (
        lambda: tools.read_file(path),
        lambda: tools.write_file(path, "overwritten"),
        lambda: tools.edit_file(path, "host secret", "changed"),
        lambda: tools.list_files(path),
    ):
        with pytest.raises(PermissionDenied):
            action()
    assert (repo.parent / "secret.txt").read_text() == "host secret"


def test_symlink_pointing_outside_is_rejected(tools, repo):
    (repo / "link.txt").symlink_to(repo.parent / "secret.txt")
    with pytest.raises(PermissionDenied, match="outside the repository"):
        tools.read_file("link.txt")
    with pytest.raises(PermissionDenied):
        tools.write_file("link.txt", "overwritten")
    assert (repo.parent / "secret.txt").read_text() == "host secret"


def test_edit_that_breaks_python_syntax_is_rejected(tools, repo):
    before = (repo / "shop" / "pricing.py").read_text()
    with pytest.raises(ToolError, match=r"syntax error in shop/pricing.py \(line 2"):
        tools.edit_file("shop/pricing.py", "return sum(prices) - 1", "return sum(prices")
    assert (repo / "shop" / "pricing.py").read_text() == before


def test_write_of_invalid_python_is_rejected_and_creates_nothing(tools, repo):
    with pytest.raises(ToolError, match="the file was not changed"):
        tools.write_file("shop/new/tax.py", "def rate(:\n    return 20\n")
    assert not (repo / "shop" / "new").exists()


def test_syntax_guardrail_only_applies_to_python_files(tools, repo):
    assert tools.write_file("notes.txt", "def rate(:").startswith("created")


def test_already_broken_python_file_may_still_be_edited(tools, repo):
    (repo / "broken.py").write_text("x = (\ny = 1\n")
    tools.edit_file("broken.py", "y = 1", "y = 2")
    assert "y = 2" in (repo / "broken.py").read_text()
