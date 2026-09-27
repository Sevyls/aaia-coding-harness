import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repository with a bug, next to a file that must stay out of reach."""
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "pricing.py").write_text(
        "def total(prices):\n    return sum(prices) - 1  # bug\n"
    )
    (root / "shop" / "cart.py").write_text(
        "from shop.pricing import total\n\n\ndef checkout(cart):\n    return total(cart)\n"
    )
    (root / "README.md").write_text("# shop\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_text("x")
    (tmp_path / "secret.txt").write_text("host secret")
    return root


@pytest.fixture
def git_repo(repo: Path) -> Path:
    """The same repository with one commit, as a harness target."""
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
    subprocess.run([*git, "init", "--quiet"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("__pycache__/\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "start"], cwd=repo, check=True)
    return repo
