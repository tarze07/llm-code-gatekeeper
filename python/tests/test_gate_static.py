"""Testy G1.static (dispatcher core-owy, `gatekeeper_core.gates.g1_static`)
na żywym ruffie/mypy — integracja, nie golden file. Adapter ma już testy na
zapisanym wyjściu (`test_adapters_linters.py`); tu sprawdzamy, że
`PythonStaticChecker` faktycznie dogfooduje się przez entry points
`gatekeeper.static_checkers` i że filtrowanie do zmienionych linii, decyzja
`pass`/`fail`, obsługa braku narzędzia działają na żywo. Testy pomijane bez
ruffa.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g1_static import StaticGuard

from gatekeeper_python.adapters.linters import PythonStaticChecker

requires_ruff = pytest.mark.skipif(
    shutil.which("ruff") is None, reason="ruff niedostępny — zainstaluj `.[gates]`"
)


@requires_ruff
def test_brak_zmienionych_plikow_pythona_przechodzi_bez_uruchamiania_narzedzi(repo):
    repo.checkout("feature", create=True)
    repo.write("README.md", "# projekt\n\nnowy opis\n")
    repo.commit("docs: opis")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["static.python_files_checked"] == 0


@requires_ruff
def test_niebezpieczny_wzorzec_w_zmienionej_linii_blokuje(repo):
    repo.checkout("feature", create=True)
    repo.write(
        "src/risky.py",
        "def load(data):\n"
        "    try:\n"
        "        return data['x']\n"
        "    except:\n"  # E722 — nie jest w RUFF_HIGH_PREFIXES
        "        pass\n",  # S110 razem z E722 -> try-except-pass, S w prefiksach -> HIGH
    )
    repo.commit("feat: ryzykowny kod")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "fail"
    assert result.facts["static.high_severity_count"] >= 1
    assert any(f.rule_id == "ruff.S110" for f in result.findings)


@requires_ruff
def test_bezpieczny_kod_przechodzi(repo):
    repo.checkout("feature", create=True)
    repo.write("src/clean.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
    repo.commit("feat: dodawanie")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({"require_mypy": False}).run(change)

    assert result.status == "pass"


@requires_ruff
def test_wymyslone_wywolanie_api_blokuje_gdy_mypy_wymagany(repo):
    """Klasa defektu, dla której ta bramka istnieje: wywołanie API, którego nie ma."""
    repo.checkout("feature", create=True)
    repo.write(
        "src/typed.py",
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def use() -> None:\n"
        "    add('x', 'y')\n",
    )
    repo.commit("feat: użycie")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({"require_mypy": True}).run(change)

    assert result.status == "fail"
    assert any(f.rule_id == "mypy.arg-type" for f in result.findings)
    assert result.facts["static.mypy_available"] is True


@requires_ruff
def test_znaleziska_poza_zmienionymi_liniami_sa_odfiltrowane(repo):
    """Defekt w linii, której PR nie ruszył, nie ma prawa zablokować tego PR-a."""
    repo.write("src/legacy.py", "import os\n\n\ndef ok():\n    return 1\n")
    repo.commit("baza z długiem")  # `os` nieużywany od zawsze
    repo.checkout("feature", create=True)
    repo.write(
        "src/legacy.py",
        "import os\n\n\ndef ok():\n    return 1\n\n\ndef nowa():\n    return 2\n",
    )
    repo.commit("feat: nowa funkcja, stary dług zostaje")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({"require_mypy": False}).run(change)

    assert result.status == "pass"
    assert not any(f.rule_id == "ruff.F401" for f in result.findings)


def test_brak_ruffa_jest_bledem_bramki(repo, monkeypatch):
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "x = 1\n")
    repo.commit("zmiana")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    monkeypatch.setattr(shutil, "which", lambda name, **kwargs: None)
    result = StaticGuard({}).run(change)

    assert result.status == "error"
    assert result.facts["static.ruff_available"] is False


# ---------------------------------------- `require_*` profilu enforcing
# (REVIEW.md §5 P1). Python nie ma odpowiednika „brakującego tsconfiga":
# ruff i mypy działają bez pliku konfiguracyjnego, więc jedyną dziurą jest
# brak narzędzia — a ten z `require_mypy: true` kończy się `error`.


@requires_ruff
def test_require_mypy_brak_mypy_jest_bledem(repo, monkeypatch):
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "x = 1\n")
    repo.commit("zmiana")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **kw: None if name == "mypy" else real_which(name, **kw),
    )
    result = PythonStaticChecker().check(change, {"require_mypy": True}, "G1.static", 60.0)

    assert result.error is not None
    assert result.facts["static.mypy_available"] is False


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("app.ts", "export const x = 1;\n"),
        ("Program.cs", "class P {}\n"),
        ("docs/notatka.md", "tekst\n"),
    ],
)
def test_zmiana_bez_pythona_nie_wymaga_narzedzi(repo, monkeypatch, path, content):
    """Repo bez Pythona w diffie nie może dostać `error` za brak ruffa/mypy."""
    repo.checkout("feature", create=True)
    repo.write(path, content)
    repo.commit("feat: nie-Python")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    monkeypatch.setattr(shutil, "which", lambda name, **kwargs: None)
    outcome = PythonStaticChecker().check(
        change, {"require_ruff": True, "require_mypy": True}, "G1.static", 60.0
    )

    assert outcome.error is None, outcome.error
    assert outcome.facts["static.python_files_checked"] == 0
