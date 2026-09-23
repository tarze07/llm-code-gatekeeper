from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
from functools import lru_cache
from pathlib import Path

import pytest


def pytest_configure() -> None:
    """Dokłada katalog binarny środowiska testowego na początek `PATH`.

    Bramki wołają `diff-cover` i resztę narzędzi jako podprocesy, przez
    `shutil.which`. Instalują się one **razem z packiem**, do `…/.venv/bin`,
    ale `…/.venv/bin/python -m pytest` *nie* dokłada tego katalogu do `PATH`
    — robi to dopiero `source …/activate`. Zestaw odpalony bez aktywacji
    zgłaszał więc „nie znaleziono programu: diff-cover" dla narzędzia
    leżącego obok własnego interpretera: ten sam haczyk, który panel obchodzi
    w `gatekeeper_web.cli.ensure_tools_on_path`.

    Katalog bierzemy z `sysconfig`, nie z `Path(sys.executable).resolve()`:
    `…/.venv/bin/python` bywa dowiązaniem do `/usr/bin/python3.12`, więc
    rozwinięcie ścieżki wyprowadza **poza** środowisko packa.

    Hook musi być `pytest_configure`, nie fixture: warunki `skipif` w
    `test_gate_diff_coverage.py` liczą się przy imporcie modułu testowego,
    czyli zanim jakikolwiek fixture zdąży się wykonać.
    """
    bindir = sysconfig.get_path("scripts")
    parts = [p for p in os.environ.get("PATH", os.defpath).split(os.pathsep) if p]
    if bindir not in parts:
        os.environ["PATH"] = os.pathsep.join([bindir, *parts])

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
    r.write("src/app.ts", "export function hello(): string {\n  return 'hi';\n}\n")
    r.commit("initial")
    return r


# --------------------------------------------------------------------------
# Fixture'y G2.* — testy integracyjne na prawdziwym vitest, nie na mockach
# (ta sama zasada co `dotnet_repo` w csharp-packu). `npm install` jest
# **jeden na sesję**, a repozytoria testowe kopiują wynik —
# instalacja per test kosztowałaby minuty zamiast sekund.
# --------------------------------------------------------------------------

_PACKAGE_JSON = """{
  "name": "fixture",
  "version": "1.0.0",
  "type": "module",
  "scripts": { "test": "vitest run" },
  "devDependencies": {
    "vitest": "^3.0.0",
    "@vitest/coverage-v8": "^3.0.0"
  }
}
"""

requires_node = pytest.mark.skipif(
    shutil.which("node") is None or shutil.which("npm") is None,
    reason="node/npm niedostępne",
)


@pytest.fixture(scope="session")
def node_modules(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    """Jeden `npm install` na sesję. `None`, gdy npm nie ma albo instalacja
    padła (brak sieci) — testy integracyjne wtedy skipują, nie czerwienią."""
    if shutil.which("npm") is None:
        return None
    root = tmp_path_factory.mktemp("node-deps")
    (root / "package.json").write_text(_PACKAGE_JSON, encoding="utf-8")
    proc = subprocess.run(
        ["npm", "install", "--silent", "--no-audit", "--no-fund"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    modules = root / "node_modules"
    return modules if proc.returncode == 0 and modules.is_dir() else None


@pytest.fixture
def ts_repo(tmp_path: Path, node_modules: Path | None) -> Repo:
    """Repo z vitestem i jedną funkcją `add` w `src/app.ts` — baza pod
    scenariusze G2.*, analogicznie do `dotnet_repo` w csharp-packu."""
    if node_modules is None:
        pytest.skip("brak zainstalowanych zależności npm (npm/sieć niedostępne)")
    path = tmp_path / "repo"
    path.mkdir(parents=True, exist_ok=True)
    r = Repo(path)
    r.write(".gitignore", "node_modules/\ncoverage/\n")
    r.write("package.json", _PACKAGE_JSON)
    r.write(
        "src/app.ts",
        "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
    )
    r.commit("baza: add")
    shutil.copytree(node_modules, path / "node_modules", symlinks=True)
    return r


@lru_cache(maxsize=1)
def _parser_available() -> bool:
    """`@typescript-eslint/parser` rozwiązywalny z globalnego `node_modules`
    — bez niego helper `G2.*` nie ma czym parsować (ten sam warunek, co
    `gatekeeper-cs-helper` w csharp-packu)."""
    if shutil.which("node") is None:
        return False
    from gatekeeper_ts.node import node_env

    proc = subprocess.run(
        ["node", "-e", "require('@typescript-eslint/parser')"],
        env=node_env(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0


requires_helper = pytest.mark.skipif(
    not _parser_available(), reason="node/@typescript-eslint/parser niedostępne"
)
