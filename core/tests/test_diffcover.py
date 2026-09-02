"""Testy `core/diffcover.py::parse_diff_cover_json` — wydzielone z python-packu
przy pojawieniu się drugiego konsumenta (csharp-pack, PLAN-G2.md §5).

Próbka `diffcover_report.json` pochodzi z realnego przebiegu: `coverage run
--branch` + `coverage xml` + `diff-cover ... --branch-coverage
--total-percent-float --format=json:...` na fixture z funkcją o dwóch
gałęziach, z których test pokrywa tylko jedną (`src/app.py:6`, `if n >= 0:`,
`missing-branches="8"` w Cobercie) — dokładnie ten przypadek, który ma
odróżnić tę bramkę od zwykłego pokrycia liniowego. Format wyjścia `diff-cover`
jest ten sam niezależnie od tego, jaki raport pokrycia (Cobertura z
`coverage.py` czy z coverlet) dostało na wejściu — stąd próbka nagrana na
Pythonie jest równie ważna dla przyszłego konsumenta C#.
"""

from __future__ import annotations

from pathlib import Path

from gatekeeper_core.core.diffcover import DiffCoverageResult, parse_diff_cover_json

REPORT = Path(__file__).parent / "data" / "diffcover_report.json"


def test_parsowanie_golden_file_liczy_pokryte_i_niepokryte_linie():
    result = parse_diff_cover_json(REPORT.read_text(encoding="utf-8"))

    assert result.files["src/app.py"].covered == 2
    assert result.files["src/app.py"].total == 4
    assert result.files["tests/test_app.py"].covered == 3
    assert result.files["tests/test_app.py"].total == 3


def test_czesciowa_galaz_liczy_sie_jako_niepokryta():
    """`--branch-coverage` w diff-cover: linia `if` z wykonaną tylko jedną
    gałęzią trafia do `violation_lines`, mimo że `hits > 0` w Cobercie."""
    result = parse_diff_cover_json(REPORT.read_text(encoding="utf-8"))

    assert result.files["src/app.py"].ratio == 0.5


def test_pusty_payload_daje_pusty_wynik():
    assert parse_diff_cover_json("") == DiffCoverageResult()


def test_brak_pliku_w_diffie_daje_none_ratio():
    result = parse_diff_cover_json('{"src_stats": {}}')
    assert result.files == {}
