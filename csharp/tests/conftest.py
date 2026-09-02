from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class Repo:
    """Minimalne repozytorium git do testów bramek."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def write(self, rel: str, content: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def checkout(self, branch: str, create: bool = False) -> None:
        self.git("checkout", "-q", *(["-b"] if create else []), branch)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir(parents=True, exist_ok=True)
    r = Repo(path)
    r.write("README.md", "# projekt\n")
    r.write("src/App.cs", "namespace App;\npublic class Program {}\n")
    r.commit("initial")
    return r
