"""Interpretacja wyniku: zadanie ≠ bramka ≠ decyzja polityki.

Scenariusze odbioru 1–3 i 12 z `PLAN-WEB-UI.md` §9.
"""

from __future__ import annotations

from conftest import load_sample

from gatekeeper_web.services.deserialize import run_result_from_payload
from gatekeeper_web.services.reports import parse_report
from gatekeeper_web.services.view import BRAK_DANYCH, RunView, build_run_view


def zbuduj(nazwa: str) -> RunView:
    import json

    parsed = parse_report(json.dumps(load_sample(nazwa)).encode("utf-8"))
    return build_run_view(
        run_result_from_payload(parsed.payload),
        project_id=1,
        project_slug="demo",
        project_name="Demo",
    )


def test_bramka_pass_lamiaca_polityke_jest_nazwana_wprost() -> None:
    view = zbuduj("demo-celowe-usterki.json")
    coverage = next(g for g in view.gates if g.gate == "G2.diff_coverage")

    # Surowy status bramki zostaje `pass` — to on jest w raporcie.
    assert coverage.status == "pass"
    # …ale panel nie udaje, że wszystko jest w porządku.
    assert coverage.needs_explanation
    assert any("polityka uznała" in note for note in coverage.notes)
    assert [r.rule for r in coverage.blocking_reasons] == ["coverage.diff_ratio"]


def test_pomiar_pokrycia_jest_pokazany_razem_z_naruszeniem() -> None:
    view = zbuduj("demo-celowe-usterki.json")
    reason = next(r for r in view.reasons if r.rule == "coverage.diff_ratio")

    assert reason.fact is not None
    assert reason.fact.display == "2,7%"
    # Sam procent nie mówi, czy to 1/37, czy 270/10000.
    assert reason.fact.detail == "1 z 37"


def test_brak_pomiaru_nie_jest_zerem() -> None:
    view = zbuduj("bez-znalezisk.json")
    coverage = next(g for g in view.gates if g.gate == "G2.diff_coverage")
    ratio = next((f for f in coverage.facts if f.key == "coverage.diff_ratio"), None)

    # Fakt nie występuje w raporcie, bo bramka niczego nie zmierzyła.
    assert ratio is None
    dostepnosc = next(f for f in coverage.facts if f.key == "coverage.tool_available")
    assert dostepnosc.display == "nie"
    assert coverage.status == "skipped"
    assert any("nie została uruchomiona" in note for note in coverage.notes)


def test_brakujacy_fakt_ma_etykiete_brak_danych() -> None:
    from gatekeeper_web.services.view import describe_fact

    fact = describe_fact("coverage.diff_ratio", {"coverage.total_lines": 37})
    assert fact.missing
    assert fact.display == BRAK_DANYCH


def test_zero_i_brak_danych_to_dwie_rozne_rzeczy() -> None:
    from gatekeeper_web.services.view import describe_fact

    zero = describe_fact("sast.finding_count", {"sast.finding_count": 0})
    assert not zero.missing
    assert zero.display == "0"


def test_bramka_w_bledzie_ostrzega_ze_nie_ma_pomiaru() -> None:
    view = zbuduj("pierwszy-przebieg.json")
    cross = next(g for g in view.gates if g.gate == "G2.cross_verify")

    assert cross.status == "error"
    assert cross.warn_only
    assert any("nie policzyła wyniku" in note for note in cross.notes)
    assert any("nie blokuje zmiany" in note for note in cross.notes)
    assert view.has_gate_errors


def test_nierozwiazane_zaleznosci_sa_widoczne_jako_fakt() -> None:
    view = zbuduj("pierwszy-przebieg.json")
    sca = next(g for g in view.gates if g.gate == "G3.sca")
    nierozwiazane = next(f for f in sca.facts if f.key == "sca.unresolved_package_count")

    # Cztery pakiety, których nie sprawdzono, to nie jest dowód braku podatności.
    assert nierozwiazane.display == "4"


def test_ograniczenie_tsc_available_nie_znika_z_widoku() -> None:
    view = zbuduj("demo-celowe-usterki.json")
    static = next(g for g in view.gates if g.gate == "G1.static")
    tsc = next(f for f in static.facts if f.key == "static.tsc_available")

    assert tsc.display == "nie"


def test_trzy_poziomy_wyniku_sa_rozdzielone() -> None:
    view = zbuduj("demo-celowe-usterki.json")

    assert view.job_state == "imported"  # zadanie: skąd wynik
    assert view.gate_status_counts == {"fail": 9, "pass": 2}  # bramki
    assert view.verdict == "BLOCK"  # polityka
    assert len(view.findings) == 22


def test_znaleziska_sa_posortowane_wagą_i_pewnoscia() -> None:
    view = zbuduj("demo-celowe-usterki.json")
    wagi = [f.severity for f in view.findings]

    assert wagi[0] == "critical"
    assert wagi[-1] in ("info", "low")


def test_powod_bez_bramki_nie_ginie() -> None:
    view = zbuduj("pierwszy-przebieg.json")

    # `paths_match` liczy się z samej zmiany, nie z bramki — bez osobnej sekcji
    # taki powód wypadłby z widoku razem z decyzją, którą uzasadnia.
    assert any(r.rule.startswith("paths_match") for r in view.unattached_reasons)


def test_wszystkie_znaleziska_maja_fingerprint_z_raportu() -> None:
    view = zbuduj("demo-celowe-usterki.json")
    fingerprints = {f.fingerprint for f in view.findings}

    assert len(fingerprints) == 22
    assert view.find_finding(next(iter(fingerprints))) is not None
