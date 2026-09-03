"""Testy G1.complexity (dispatcher core-owy) na fałszywym analizatorze —
core sam nie ma żadnego `ComplexityAnalyzer` (ten żyje w pack'ach), więc
`_installed_analyzers` jest tu jawnie podmieniane, tak jak sugeruje
PLAN-G1-complexity.md §9 (PR A, punkt 5).
"""

from __future__ import annotations

from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.plugins import ComplexityOutcome, MethodComplexity
from gatekeeper_core.gates import g1_complexity
from gatekeeper_core.gates.g1_complexity import ComplexityGuard


class FakeAnalyzer:
    analyzer_id = "fake"
    languages = ("python",)

    def __init__(self, methods=None, error=None):
        self._methods = methods or []
        self._error = error

    def empty_facts(self):
        return {"complexity.fake_files_checked": 0}

    def analyze(self, change, config, gate_id, budget_s):
        return ComplexityOutcome(methods=self._methods, facts={}, error=self._error)


def _change(repo, path: str, base_content: str, head_content: str) -> ChangeContext:
    repo.write(path, base_content)
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write(path, head_content)
    repo.commit("feat: zmiana")
    return ChangeContext.from_git(repo.path, "main", "HEAD")


def test_brak_analizatora_daje_skipped(repo, monkeypatch):
    monkeypatch.setattr(g1_complexity, "_installed_analyzers", lambda: [])
    change = _change(repo, "src/app.py", "x = 1\n", "x = 2\n")

    result = ComplexityGuard({}).run(change)

    assert result.status == "skipped"
    assert result.facts["complexity.max"] == 0


def test_metoda_powyzej_progu_blokuje(repo, monkeypatch):
    change = _change(repo, "src/app.py", "x = 1\n", "def f():\n    pass\n")
    method = MethodComplexity(file="src/app.py", name="f", lineno=1, end_lineno=2, complexity=11)
    monkeypatch.setattr(g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer([method])])

    result = ComplexityGuard({}).run(change)

    assert result.status == "fail"
    assert result.facts["complexity.max"] == 11
    assert result.facts["complexity.over_threshold_count"] == 1
    finding = result.findings[0]
    assert finding.rule_id == "complexity.too_high"
    assert finding.severity == "medium"
    assert finding.evidence["complexity"] == 11
    assert finding.evidence["threshold"] == 10


def test_metoda_na_progu_przechodzi(repo, monkeypatch):
    change = _change(repo, "src/app.py", "x = 1\n", "def f():\n    pass\n")
    method = MethodComplexity(file="src/app.py", name="f", lineno=1, end_lineno=2, complexity=10)
    monkeypatch.setattr(g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer([method])])

    result = ComplexityGuard({}).run(change)

    assert result.status == "pass"
    assert result.findings == []


def test_wysoka_zlozonosc_ma_severity_high(repo, monkeypatch):
    change = _change(repo, "src/app.py", "x = 1\n", "def f():\n    pass\n")
    method = MethodComplexity(file="src/app.py", name="f", lineno=1, end_lineno=2, complexity=21)
    monkeypatch.setattr(g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer([method])])

    result = ComplexityGuard({}).run(change)

    assert result.findings[0].severity == "high"


def test_metoda_poza_diffem_nie_jest_liczona(repo, monkeypatch):
    """Nietknięta funkcja w tym samym pliku, nawet gdy plik ma inne zmiany,
    nie ma prawa trafić do raportu — inaczej pierwszy przebieg na starym
    repo umiera (PLAN-G1-complexity.md §4)."""
    change = _change(
        repo,
        "src/app.py",
        "def stara():\n    pass\n",
        "def stara():\n    pass\n\n\ndef nowa():\n    pass\n",
    )
    stara = MethodComplexity(
        file="src/app.py", name="stara", lineno=1, end_lineno=2, complexity=25
    )
    nowa = MethodComplexity(file="src/app.py", name="nowa", lineno=5, end_lineno=6, complexity=1)
    monkeypatch.setattr(
        g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer([stara, nowa])]
    )

    result = ComplexityGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["complexity.methods_measured"] == 1
    assert result.facts["complexity.max"] == 1


def test_plik_testowy_pomijany_domyslnie(repo, monkeypatch):
    change = _change(repo, "tests/test_app.py", "x = 1\n", "def test_f():\n    pass\n")
    method = MethodComplexity(
        file="tests/test_app.py", name="test_f", lineno=1, end_lineno=2, complexity=25
    )
    monkeypatch.setattr(g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer([method])])

    result = ComplexityGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["complexity.methods_measured"] == 0


def test_blad_analizatora_daje_status_error(repo, monkeypatch):
    change = _change(repo, "src/app.py", "x = 1\n", "x = 2\n")
    monkeypatch.setattr(
        g1_complexity, "_installed_analyzers", lambda: [FakeAnalyzer(error="parser padł")]
    )

    result = ComplexityGuard({}).run(change)

    assert result.status == "error"
