"""Testy harnessu kalibracyjnego — logika, metryki i pełny benchmark.

Sprawdzamy parsowanie przypadków, obliczanie metryk precyzji i wskaźnika ucieczki,
statystyki per bramka, renderowanie raportu Markdown oraz budowę repozytoriów fixture'ów.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gatekeeper_core.calibration import (
    Case,
    CaseResult,
    Expectation,
    GateMetric,
    _build_case_repo,
    _check,
    gate_for_rule,
    load_cases,
    run_calibration,
)
from gatekeeper_core.core.finding import (
    Decision,
    GateResult,
    Reason,
    RunResult,
    Verdict,
)
from gatekeeper_core.core.policy import Policy


def _run(verdict: Verdict, reasons=(), warnings=()) -> RunResult:
    return RunResult(
        run_id="x",
        repo=".",
        base_sha="a" * 40,
        head_sha="b" * 40,
        gate_results=[
            GateResult(gate="G0.scope", status="pass"),
            GateResult(gate="G1.deps", status="pass"),
            GateResult(gate="G3.secrets", status="pass"),
        ],
        decision=Decision(verdict=verdict, reasons=list(reasons), warnings=list(warnings)),
    )


def _case(**expect_kwargs) -> Case:
    return Case(name="t", description="", fixture="t", expect=Expectation(**expect_kwargs))


def test_wczytywanie_prawdziwego_pliku_cases_yaml():
    cases = load_cases(Path("calibration/cases.yaml"))
    assert len(cases) >= 35
    names = {c.name for c in cases}
    assert {
        "halucynowany-pakiet",
        "halucynowany-pakiet-npm",
        "halucynowany-pakiet-nuget",
        "czysty-pr",
        "diff-zbyt-duzy",
        "sekret-w-diffie",
        "typosquat-pypi",
        "typosquat-npm",
        "typosquat-nuget",
    }.issubset(names)

    hp = next(c for c in cases if c.name == "halucynowany-pakiet")
    assert hp.expect.verdict is Verdict.BLOCK
    assert hp.expect.blocking_rules == ("deps.unknown_package",)

    clean = next(c for c in cases if c.name == "czysty-pr")
    assert clean.expect.verdict is Verdict.PASS

    big_diff = next(c for c in cases if c.name == "diff-zbyt-duzy")
    assert big_diff.expect.verdict is Verdict.BLOCK
    assert big_diff.expect.blocking_rules == ("diff.effective_lines",)


def test_check_zgodny_werdykt_i_reguly_przechodzi():
    case = _case(verdict=Verdict.BLOCK, blocking_rules=("deps.unknown_package",))
    reason = Reason(source="blocking", rule="deps.unknown_package", detail="x")
    run = _run(Verdict.BLOCK, reasons=[reason])

    result = _check(case, run)

    assert result.passed
    assert result.reason == ""


def test_check_zly_werdykt_daje_czytelny_powod():
    case = _case(verdict=Verdict.PASS)
    run = _run(Verdict.BLOCK)

    result = _check(case, run)

    assert not result.passed
    assert "PASS" in result.reason
    assert "BLOCK" in result.reason


def test_check_brakujaca_regula_blokujaca_failuje():
    case = _case(verdict=Verdict.BLOCK, blocking_rules=("deps.unknown_package",))
    run = _run(Verdict.BLOCK, reasons=[Reason(source="blocking", rule="inna.regula", detail="x")])

    result = _check(case, run)

    assert not result.passed
    assert "deps.unknown_package" in result.reason


def test_check_brakujace_ostrzezenie_failuje():
    case = _case(verdict=Verdict.PASS, warning_rules=("sast.critical_count",))
    run = _run(Verdict.PASS)

    result = _check(case, run)

    assert not result.passed
    assert "sast.critical_count" in result.reason


def test_gate_for_rule_mapping():
    assert gate_for_rule("diff.effective_lines") == "G0.scope"
    assert gate_for_rule("scope.protected_paths") == "G0.scope"
    assert gate_for_rule("deps.unknown_package") == "G1.deps"
    assert gate_for_rule("complexity.max") == "G1.complexity"
    assert gate_for_rule("secrets.found_in_diff") == "G3.secrets"
    assert gate_for_rule("nieznana.regula") == "nieznana"


def test_calibration_metrics_obliczenia():
    from gatekeeper_core.calibration import CalibrationMetrics

    # Puste metryki nie dzielą przez zero
    empty = CalibrationMetrics()
    assert empty.total == 0
    assert empty.precision_block is None
    assert empty.escape_rate is None
    assert empty.accuracy is None

    # Typowy scenariusz kalibracji: 10 TP, 0 FP, 20 TN, 1 FN
    m = CalibrationMetrics(tp=10, fp=0, tn=20, fn=1)
    assert m.total == 31
    assert m.precision_block == 1.0  # 10 / (10 + 0) = 100%
    assert round(m.escape_rate, 3) == round(1 / 11, 3)  # ~9.1%
    assert round(m.accuracy, 3) == round(30 / 31, 3)  # ~96.8%


def test_gate_metric_obliczenia():
    gm = GateMetric(
        gate="G1.deps",
        evaluated_count=10,
        blocked_tp=4,
        blocked_fp=1,
        assigned_defects=5,
        detected_defects=4,
    )
    assert gm.blocked_total == 5
    assert gm.precision == 4 / 5
    assert gm.detection_rate == 4 / 5

    empty_gm = GateMetric(gate="G0.scope")
    assert empty_gm.precision is None
    assert empty_gm.detection_rate is None


def test_calibration_report_render_zawiera_sekcje():
    from gatekeeper_core.calibration import CalibrationReport

    c1 = Case(
        name="case-1",
        description="",
        fixture="f1",
        expect=Expectation(verdict=Verdict.BLOCK, blocking_rules=("diff.effective_lines",)),
    )
    r1 = _run(
        Verdict.BLOCK,
        reasons=[Reason(source="blocking", rule="diff.effective_lines", detail="diff")],
    )

    c2 = Case(name="case-2", description="", fixture="f2", expect=Expectation(verdict=Verdict.PASS))
    r2 = _run(Verdict.PASS)

    report = CalibrationReport(
        results=[
            CaseResult(case=c1, passed=True, run=r1),
            CaseResult(case=c2, passed=True, run=r2),
            CaseResult(
                case=Case(
                    name="case-3",
                    description="",
                    fixture="f3",
                    expect=Expectation(verdict=Verdict.PASS),
                ),
                passed=True,
                skipped=True,
                reason="narzędzie niedostępne",
            ),
        ]
    )

    rendered = report.render()
    assert "## Podsumowanie metryk" in rendered
    assert "Precyzja decyzji BLOCK" in rendered
    assert "Escape rate" in rendered
    assert "## Skuteczność bramek" in rendered
    assert "| Bramka | Ocenione | Zablokowane (TP/FP) | Wykryte defekty | Skuteczność |" in rendered
    assert "## Status przypadków" in rendered
    assert "✅ `case-1` [BLOCK]" in rendered
    assert "✅ `case-2` [PASS]" in rendered
    assert "⏭️ `case-3`" in rendered
    assert "2/2 przypadków zgodnych z oczekiwaniem (1 pominiętych — brak narzędzia)" in rendered


def test_budowa_repo_z_base_head_odzwierciedla_usuniecie_pliku(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "base").mkdir(parents=True)
    (fixture / "head").mkdir(parents=True)
    (fixture / "base" / "old.py").write_text("x = 1\n")
    (fixture / "head" / "new.py").write_text("y = 2\n")

    with _build_case_repo(fixture) as (repo_path, base_sha, head_sha):
        assert (repo_path / "new.py").exists()
        assert not (repo_path / "old.py").exists()
        assert base_sha != head_sha


def test_budowa_repo_z_commit_message_txt(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "base").mkdir(parents=True)
    (fixture / "head").mkdir(parents=True)
    (fixture / "base" / "file.py").write_text("a = 1\n")
    (fixture / "head" / "file.py").write_text("a = 2\n")
    (fixture / "head" / "commit_message.txt").write_text("custom commit trailer\n")

    with _build_case_repo(fixture) as (repo_path, base_sha, head_sha):
        assert not (repo_path / "commit_message.txt").exists()


def test_brak_fixture_daje_czytelny_blad(tmp_path):
    with pytest.raises(FileNotFoundError), _build_case_repo(tmp_path / "nie-ma-takiego"):
        pass


def test_pelny_benchmark_osiaga_wymagana_precyzje_i_escape_rate():
    cases_path = Path("calibration/cases.yaml")
    if not cases_path.exists():
        cases_path = Path("core/calibration/cases.yaml")
    fixtures_dir = Path("calibration/fixtures")
    if not fixtures_dir.exists():
        fixtures_dir = Path("core/calibration/fixtures")
    policy_path = Path("policy/gates.yaml")
    if not policy_path.exists():
        policy_path = Path("core/policy/gates.yaml")

    cases = load_cases(cases_path)
    policy = Policy.load(policy_path)
    report = run_calibration(cases, policy, fixtures_dir)

    assert report.ok, f"Porażka kalibracji: {report.failed}"
    m = report.metrics
    assert m.precision_block is not None
    assert m.precision_block >= 0.80, f"Precyzja {m.precision_block:.1%} poniżej 80%"
    assert m.escape_rate is not None
    assert m.escape_rate <= 0.15, f"Wskaźnik ucieczki {m.escape_rate:.1%} powyżej 15%"
