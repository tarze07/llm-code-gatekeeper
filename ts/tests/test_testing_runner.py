"""Testy `testing/runner.py` — wykrywanie runnera i parser wyniku.

`parse_report` jest czystą funkcją i dlatego jest testowany na **zapisanej
próbce** prawdziwego JSON-a vitesta (`tests/data/vitest_report.json`), tak
samo jak `parse_trx` w csharp-packu. Uruchomienie prawdziwego vitesta
sprawdzają testy bramek (`test_gate_crossverify.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gatekeeper_ts.testing.runner import (
    TestRunnerUnavailable,
    detect_runner,
    parse_report,
)

DATA = Path(__file__).parent / "data" / "vitest_report.json"
ROOT = Path("/repo")


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_wykrywa_vitest_z_devdependencies(tmp_path):
    root = _write_manifest(tmp_path, {"devDependencies": {"vitest": "^3"}})
    assert detect_runner(root) == "vitest"


def test_wykrywa_jest_z_dependencies(tmp_path):
    assert detect_runner(_write_manifest(tmp_path, {"dependencies": {"jest": "^29"}})) == "jest"


def test_wykrywa_runner_ze_skryptu_test(tmp_path):
    """Repo bywa skonfigurowane tak, że runner jest zależnością przechodnią
    (preset frameworka), a jedyną deklaracją jest `scripts.test`."""
    assert detect_runner(_write_manifest(tmp_path, {"scripts": {"test": "jest --ci"}})) == "jest"


def test_vitest_wygrywa_gdy_repo_deklaruje_oba(tmp_path):
    root = _write_manifest(tmp_path, {"devDependencies": {"jest": "^29", "vitest": "^3"}})
    assert detect_runner(root) == "vitest"


def test_brak_obslugiwanego_runnera_to_blad_nie_cichy_pass(tmp_path):
    """Fail-closed: repo na mocha dostaje `error` z nazwą przyczyny, nie
    zielone „nic nie znaleziono"."""
    root = _write_manifest(tmp_path, {"devDependencies": {"mocha": "^10"}})
    with pytest.raises(TestRunnerUnavailable, match="vitest.*jest"):
        detect_runner(root)


def test_brak_package_json_to_blad(tmp_path):
    with pytest.raises(TestRunnerUnavailable, match="package.json"):
        detect_runner(tmp_path)


def test_parse_report_koreluje_wynik_po_nodeid():
    expected = [
        "tests/app.test.ts::classify > zwraca non-negative dla dodatnich",
        "tests/app.test.ts::add > dodaje — ten test przechodzi tez na starym kodzie",
    ]

    outcomes = parse_report(DATA.read_text(encoding="utf-8"), ROOT, expected)

    assert {n: o.outcome for n, o in outcomes.items()} == {
        expected[0]: "passed",
        expected[1]: "passed",
    }


def test_parse_report_pomija_testy_spoza_listy():
    """Istniejące testy w tym samym pliku są uruchamiane razem z nowymi
    (nie filtrujemy po nazwie) — ich wynik nie ma prawa wyciec do bramki."""
    outcomes = parse_report(
        DATA.read_text(encoding="utf-8"),
        ROOT,
        ["tests/app.test.ts::classify > zwraca negative dla ujemnych"],
    )

    assert list(outcomes) == ["tests/app.test.ts::classify > zwraca negative dla ujemnych"]


def test_plik_ktory_sie_nie_zaladowal_daje_error_nie_missing():
    """Rozróżnienie z punktu 3 docstringa `gates/g2_crossverify.py`: test,
    który nie zaimportował nieistniejącego jeszcze modułu, dowodzi znacznie
    mniej niż test, który poległ na asercji — i ma być liczony osobno."""
    nodeid = "tests/broken.test.ts::cokolwiek"

    outcomes = parse_report(DATA.read_text(encoding="utf-8"), ROOT, [nodeid])

    assert outcomes[nodeid].outcome == "error"
    assert "Failed to load url" in outcomes[nodeid].message


def test_pusty_albo_niepoprawny_raport_nie_wywraca_parsera():
    assert parse_report("", ROOT, ["a::b"]) == {}
    assert parse_report("to nie jest JSON", ROOT, ["a::b"]) == {}


@pytest.mark.parametrize(
    ("status", "oczekiwany"),
    [("passed", "passed"), ("failed", "failed"), ("pending", "skipped"), ("todo", "skipped")],
)
def test_mapowanie_statusow(status, oczekiwany):
    payload = json.dumps(
        {
            "testResults": [
                {
                    "name": "/repo/a.test.ts",
                    "assertionResults": [
                        {"ancestorTitles": [], "title": "x", "status": status}
                    ],
                }
            ]
        }
    )

    assert parse_report(payload, ROOT, ["a.test.ts::x"])["a.test.ts::x"].outcome == oczekiwany
