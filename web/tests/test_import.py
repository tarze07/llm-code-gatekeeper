"""Import raportu: co panel przyjmuje, co odrzuca i czego nie dopisuje.

Scenariusze odbioru 1–3 i 10 z `PLAN-WEB-UI.md` §9.
"""

from __future__ import annotations

import json

from conftest import Panel, load_sample, load_sample_bytes

from gatekeeper_web.services.reports import (
    FORMAT_V0,
    MAX_BYTES,
    ReportImportError,
    parse_report,
)


def test_import_zachowuje_wszystkie_22_znaleziska(panel: Panel) -> None:
    project_id = panel.create_project()
    response = panel.import_sample("demo-celowe-usterki.json", project_id)

    assert response.status_code == 201, response.text
    run = response.json()["run"]
    assert run["finding_count"] == 22
    assert run["gate_count"] == 11
    assert run["verdict"] == "BLOCK"
    assert run["origin"] == "imported"


def test_import_zachowuje_powody_block_i_ograniczenia(panel: Panel) -> None:
    project_id = panel.create_project()
    panel.import_sample("demo-celowe-usterki.json", project_id)
    report = panel.get(f"/api/v1/projects/{project_id}/runs/35e102d0d126").json()["report"]

    assert len(report["decision"]["reasons"]) == 13
    # „Czego ta brama nie sprawdza" jest częścią wyniku, nie ozdobnikiem —
    # raport, który to gubi, produkuje fałszywe poczucie bezpieczeństwa.
    assert len(report["not_checked"]) == 7


def test_stary_format_bez_numeru_wersji_ma_jawny_adapter() -> None:
    parsed = parse_report(load_sample_bytes("demo-celowe-usterki.json"))
    assert parsed.format_version == FORMAT_V0


def test_nieznana_wersja_formatu_jest_odrzucona() -> None:
    data = load_sample("bez-znalezisk.json")
    data["report_version"] = 99
    try:
        parse_report(json.dumps(data).encode("utf-8"))
    except ReportImportError as exc:
        assert "nieobsługiwana wersja formatu" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("panel przyjął format, którego nie zna")


def test_powtorny_import_nie_tworzy_duplikatu(panel: Panel) -> None:
    project_id = panel.create_project()
    panel.import_sample("demo-celowe-usterki.json", project_id)
    second = panel.import_sample("demo-celowe-usterki.json", project_id)

    assert second.status_code == 201
    assert second.json()["created"] is False
    runs = panel.get(f"/api/v1/projects/{project_id}/runs").json()
    assert runs["total"] == 1


def test_ten_sam_run_id_z_inna_trescia_to_konflikt(panel: Panel) -> None:
    project_id = panel.create_project()
    panel.import_sample("demo-celowe-usterki.json", project_id)

    zmieniony = load_sample("demo-celowe-usterki.json")
    zmieniony["duration_s"] = 999.0
    response = panel.import_bytes(json.dumps(zmieniony).encode("utf-8"), project_id)

    assert response.status_code == 409
    assert "inną treścią" in response.json()["detail"]


def test_ten_sam_raport_w_dwoch_projektach_jest_dozwolony(panel: Panel) -> None:
    pierwszy = panel.create_project("Projekt A")
    drugi = panel.create_project("Projekt B")
    assert panel.import_sample("demo-celowe-usterki.json", pierwszy).status_code == 201
    assert panel.import_sample("demo-celowe-usterki.json", drugi).status_code == 201

    for project_id in (pierwszy, drugi):
        assert panel.get(f"/api/v1/projects/{project_id}/runs").json()["total"] == 1


def test_zbyt_duzy_raport_jest_odrzucony(panel: Panel) -> None:
    project_id = panel.create_project()
    response = panel.import_bytes(b"x" * (MAX_BYTES + 10), project_id)
    assert response.status_code == 422
    assert "limit" in response.json()["detail"]


def test_niepoprawny_json_ma_czytelny_komunikat(panel: Panel) -> None:
    project_id = panel.create_project()
    response = panel.import_bytes(b'{"run_id": ', project_id)
    assert response.status_code == 422
    assert "niepoprawny JSON" in response.json()["detail"]


def test_plik_bez_sekcji_decision_nie_jest_raportem(panel: Panel) -> None:
    project_id = panel.create_project()
    response = panel.import_bytes(b'{"run_id": "abc"}', project_id)
    assert response.status_code == 422
    assert "decision" in response.json()["detail"]


def test_znalezisko_bez_scenariusza_awarii_jest_odrzucone(panel: Panel) -> None:
    data = load_sample("demo-celowe-usterki.json")
    data["gates"][0]["findings"][0]["failure_scenario"] = "   "
    response = panel.import_bytes(json.dumps(data).encode("utf-8"), panel.create_project())
    assert response.status_code == 422
    assert "failure_scenario" in response.json()["detail"]


def test_nieznany_status_bramki_jest_odrzucony(panel: Panel) -> None:
    data = load_sample("bez-znalezisk.json")
    data["gates"][0]["status"] = "zielono"
    response = panel.import_bytes(json.dumps(data).encode("utf-8"), panel.create_project())
    assert response.status_code == 422
    assert "status bramki" in response.json()["detail"]


def test_import_do_nieznanego_projektu_konczy_sie_404(panel: Panel) -> None:
    response = panel.import_sample("bez-znalezisk.json", 987)
    assert response.status_code == 404


def test_redakcja_usuwa_sciezki_katalogow_roboczych() -> None:
    parsed = parse_report(load_sample_bytes("demo-celowe-usterki.json"))
    secrets_gate = next(g for g in parsed.payload["gates"] if g["gate"] == "G3.secrets")
    evidence = secrets_gate["findings"][0]["evidence"]

    assert "/tmp/gatekeeper-wt-" not in json.dumps(evidence)
    assert "synthetic-secret.env" in evidence["tool_fingerprint"]


def test_redakcja_ukrywa_pola_o_nazwie_sugerujacej_sekret() -> None:
    data = load_sample("bez-znalezisk.json")
    data["gates"][0]["facts"]["scope.api_key"] = "AKIAI44QH8DHBEXAMPLE"
    parsed = parse_report(json.dumps(data).encode("utf-8"))

    assert parsed.payload["gates"][0]["facts"]["scope.api_key"] == "[zredagowano]"


def test_sciezki_artefaktow_nie_trafiaja_do_panelu() -> None:
    data = load_sample("bez-znalezisk.json")
    data["gates"][0]["artifacts"] = ["/tmp/gatekeeper-wt-abc/wyniki.xml"]
    parsed = parse_report(json.dumps(data).encode("utf-8"))

    # Artefakt istnieje tylko w katalogu roboczym bramki. Pokazanie ścieżki
    # sugerowałoby, że da się ją pobrać — nie da się (plan §8).
    assert "artifacts" not in parsed.payload["gates"][0]
