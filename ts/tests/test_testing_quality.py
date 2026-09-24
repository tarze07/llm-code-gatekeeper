"""Testy `testing/quality.py` — pięć reguł atrap testowych, te same
`rule_id` co w python- i csharp-packu, przełożone na `expect(...).matcher()`.

Każda reguła ma tu **parę**: przypadek, który ma strzelić, i sąsiedni
przypadek, który strzelić nie może. Reguła bez testu negatywnego prędzej
czy później zaczyna blokować poprawny kod — to ta sama zasada, którą
`semgrep --test` egzekwuje dla reguł SAST tego pack'a.
"""

from __future__ import annotations

from pathlib import Path

from conftest import requires_helper
from gatekeeper_core.core.finding import Severity

from gatekeeper_ts.testing.quality import lint_quality


def _rules(tmp_path: Path, source: str) -> set[str]:
    (tmp_path / "a.test.ts").write_text(source, encoding="utf-8")
    issues = lint_quality(tmp_path, ["a.test.ts"])
    return {issue.rule_id for group in issues.values() for issue in group}


@requires_helper
def test_brak_asercji(tmp_path):
    assert "test.no_assertion" in _rules(
        tmp_path, 'it("wola kod", () => {\n  compute(1);\n});\n'
    )


@requires_helper
def test_asercja_obecna_nie_jest_zgloszeniem(tmp_path):
    assert "test.no_assertion" not in _rules(
        tmp_path, 'it("sprawdza", () => {\n  expect(compute(1)).toBe(2);\n});\n'
    )


@requires_helper
def test_asercja_na_stalej(tmp_path):
    assert "test.constant_assertion" in _rules(
        tmp_path, 'it("nic nie sprawdza", () => {\n  expect(true).toBe(true);\n});\n'
    )


@requires_helper
def test_asercja_x_rowna_sie_x(tmp_path):
    """`expect(wynik).toBe(wynik)` przechodzi po usunięciu implementacji."""
    assert "test.constant_assertion" in _rules(
        tmp_path,
        'it("porownuje ze soba", () => {\n'
        "  const wynik = compute(1);\n"
        "  expect(wynik).toBe(wynik);\n});\n",
    )


@requires_helper
def test_porownanie_dwoch_roznych_wartosci_nie_jest_stala(tmp_path):
    assert "test.constant_assertion" not in _rules(
        tmp_path, 'it("ok", () => {\n  expect(compute(1)).toBe(2);\n});\n'
    )


@requires_helper
def test_mock_porownany_z_wlasnym_return(tmp_path):
    assert "test.mock_echo" in _rules(
        tmp_path,
        'it("echo", () => {\n'
        "  const dep = vi.fn();\n"
        "  dep.mockReturnValue(42);\n"
        "  expect(dep()).toBe(42);\n});\n",
    )


@requires_helper
def test_mock_uzyty_jako_zaleznosc_nie_jest_echem(tmp_path):
    """Mock wstrzyknięty do testowanego kodu, a asercja na *jego* wyniku —
    to poprawny wzorzec i nie ma prawa być zgłaszany."""
    assert "test.mock_echo" not in _rules(
        tmp_path,
        'it("uzywa mocka jako zaleznosci", () => {\n'
        "  const dep = vi.fn();\n"
        "  dep.mockReturnValue(2);\n"
        "  expect(suma(dep, 3)).toBe(5);\n});\n",
    )


@requires_helper
def test_tylko_smoke(tmp_path):
    assert "test.only_smoke" in _rules(
        tmp_path, 'it("istnieje", () => {\n  expect(compute(1)).toBeDefined();\n});\n'
    )


@requires_helper
def test_smoke_plus_realna_asercja_nie_jest_zgloszeniem(tmp_path):
    assert "test.only_smoke" not in _rules(
        tmp_path,
        'it("istnieje i ma wartosc", () => {\n'
        "  const w = compute(1);\n"
        "  expect(w).toBeDefined();\n"
        "  expect(w).toBe(2);\n});\n",
    )


@requires_helper
def test_polkniety_wyjatek(tmp_path):
    assert "test.exception_swallowed" in _rules(
        tmp_path,
        'it("polyka", () => {\n'
        "  try {\n    compute(1);\n  } catch (e) {}\n"
        "  expect(1).toBe(1);\n});\n",
    )


@requires_helper
def test_catch_z_asercja_nie_jest_polknieciem(tmp_path):
    assert "test.exception_swallowed" not in _rules(
        tmp_path,
        'it("sprawdza wyjatek", () => {\n'
        "  try {\n    compute(1);\n  } catch (e) {\n"
        "    expect(e.message).toBe('boom');\n  }\n});\n",
    )


@requires_helper
def test_znalezisko_niesie_nodeid_severity_i_linie(tmp_path):
    """Kontrakt z `gates/g2_test_sanity.py::_to_finding`, który czyta
    `evidence["line"]` i wstawia je wprost do `Finding.line`."""
    (tmp_path / "a.test.ts").write_text(
        'describe("grupa", () => {\n  it("stala", () => {\n'
        "    expect(true).toBe(true);\n  });\n});\n",
        encoding="utf-8",
    )

    issues = lint_quality(tmp_path, ["a.test.ts"])

    [issue] = issues["a.test.ts::grupa > stala"]
    assert issue.rule_id == "test.constant_assertion"
    assert issue.severity is Severity.HIGH
    assert issue.evidence["line"] == 3
