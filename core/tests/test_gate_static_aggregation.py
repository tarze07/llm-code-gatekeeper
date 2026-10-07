"""G1.static jako agregator: awaria jednego checkera nie ucisza pozostałych."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gatekeeper_core.core.change import ChangeContext, ChangedFile
from gatekeeper_core.core.finding import Finding, Severity
from gatekeeper_core.core.plugins import StaticCheckOutcome
from gatekeeper_core.gates import g1_static
from gatekeeper_core.gates.g1_static import StaticGuard


class FakeChecker:
    languages: tuple[str, ...] = ()

    def __init__(self, checker_id: str, outcome: StaticCheckOutcome) -> None:
        self.checker_id = checker_id
        self.outcome = outcome
        self.calls = 0

    def empty_facts(self) -> dict[str, Any]:
        return {f"static.{self.checker_id}_checked": 0}

    def check(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> StaticCheckOutcome:
        self.calls += 1
        return self.outcome


def _finding(path: str, line: int, severity: str = "high") -> Finding:
    return Finding(
        gate="G1.static",
        rule_id="x",
        severity=Severity.parse(severity),
        title=f"blad w {path}",
        failure_scenario="nie skompiluje sie",
        file=path,
        line=line,
    )


@pytest.fixture
def change() -> ChangeContext:
    return ChangeContext(
        repo=Path("."),
        base_sha="a",
        head_sha="b",
        files=[
            ChangedFile(path="a.py", status="M", added_lines={10}),
            ChangedFile(path="b.ts", status="M", added_lines={5}),
        ],
    )


def _run(monkeypatch: pytest.MonkeyPatch, change: ChangeContext, *checkers: FakeChecker):
    monkeypatch.setattr(g1_static, "_installed_checkers", lambda: list(checkers))
    return StaticGuard().run(change)


def test_pierwszy_checker_pada_drugi_nadal_dziala(monkeypatch, change):
    py = FakeChecker("python", StaticCheckOutcome(error="brak ruff w PATH"))
    ts = FakeChecker(
        "ts",
        StaticCheckOutcome(findings=[_finding("b.ts", 5)], facts={"static.ts_checked": 1}),
    )
    result = _run(monkeypatch, change, py, ts)

    assert ts.calls == 1
    assert result.status == "error"
    assert "python: brak ruff w PATH" in result.message
    assert result.facts["static.ts_checked"] == 1
    assert [f.file for f in result.findings] == ["b.ts"]
    assert result.facts["static.finding_count"] == 1
    assert result.facts["static.high_severity_count"] == 1


def test_dwa_bledy_oba_w_komunikacie(monkeypatch, change):
    py = FakeChecker("python", StaticCheckOutcome(error="mypy padl"))
    ts = FakeChecker("ts", StaticCheckOutcome(error="tsc nie znaleziony"))
    result = _run(monkeypatch, change, py, ts)

    assert py.calls == ts.calls == 1
    assert result.status == "error"
    assert "python: mypy padl" in result.message
    assert "ts: tsc nie znaleziony" in result.message


def test_sciezka_bledu_tez_filtruje_do_zmienionych_linii(monkeypatch, change):
    py = FakeChecker("python", StaticCheckOutcome(error="padl"))
    ts = FakeChecker(
        "ts",
        StaticCheckOutcome(findings=[_finding("b.ts", 5), _finding("b.ts", 500)]),
    )
    result = _run(monkeypatch, change, py, ts)

    assert result.status == "error"
    assert [f.line for f in result.findings] == [5]


def test_bez_bledow_bez_zmian(monkeypatch, change):
    py = FakeChecker("python", StaticCheckOutcome(findings=[_finding("a.py", 10, "low")]))
    result = _run(monkeypatch, change, py)
    assert result.status == "pass"
    assert result.facts["static.finding_count"] == 1
