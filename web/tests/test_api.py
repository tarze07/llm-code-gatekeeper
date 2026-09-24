"""API v1: historia, filtry, stronicowanie, eksport."""

from __future__ import annotations

import json

from conftest import Panel, load_sample


def test_lista_projektow_i_archiwizacja(panel: Panel) -> None:
    project_id = panel.create_project("Taskboard")

    assert panel.patch_json(f"/api/v1/projects/{project_id}", {"archived": True}).status_code == 200
    aktywne = panel.get("/api/v1/projects").json()["projects"]
    assert aktywne == []

    wszystkie = panel.get("/api/v1/projects?include_archived=true").json()["projects"]
    assert [p["name"] for p in wszystkie] == ["Taskboard"]
    # Archiwizacja ukrywa projekt, nie kasuje historii.
    assert panel.get(f"/api/v1/projects/{project_id}/runs").json()["total"] == 0


def test_projekt_bez_nazwy_jest_odrzucony(panel: Panel) -> None:
    response = panel.post_json("/api/v1/projects", {"name": "  "})
    assert response.status_code == 422


def test_historia_filtruje_po_decyzji(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    panel.import_sample("bez-znalezisk.json", project_id)

    wszystkie = panel.get(f"/api/v1/projects/{project_id}/runs").json()
    assert wszystkie["total"] == 2

    zablokowane = panel.get(f"/api/v1/projects/{project_id}/runs?verdict=BLOCK").json()
    assert zablokowane["total"] == 1
    assert zablokowane["runs"][0]["verdict"] == "BLOCK"


def test_historia_stronicuje(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    panel.import_sample("bez-znalezisk.json", project_id)

    strona = panel.get(f"/api/v1/projects/{project_id}/runs?limit=1&offset=1").json()
    assert strona["total"] == 2
    assert len(strona["runs"]) == 1


def test_historia_szuka_po_sha(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    trafienie = panel.get(f"/api/v1/projects/{project_id}/runs?q=b64b59ef").json()
    assert trafienie["total"] == 1
    pudlo = panel.get(f"/api/v1/projects/{project_id}/runs?q=zzzzzzzz").json()
    assert pudlo["total"] == 0


def test_niepoprawna_decyzja_w_filtrze_jest_odrzucona(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    assert panel.get(f"/api/v1/projects/{project_id}/runs?verdict=OK").status_code == 422


def test_pelny_wynik_zawiera_caly_raport(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    body = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()

    assert body["api_version"] == "1"
    assert sum(len(g["findings"]) for g in body["report"]["gates"]) == 22
    assert body["report"]["decision"]["verdict"] == "BLOCK"


def test_eksport_w_trzech_formatach(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    baza = f"/api/v1/projects/{project_id}/runs/{run_id}/report"

    json_response = panel.get(f"{baza}?format=json")
    assert json_response.status_code == 200
    assert json.loads(json_response.text)["run_id"] == run_id

    markdown = panel.get(f"{baza}?format=markdown").text
    # Panel nie stosuje limitu dziesięciu znalezisk z komentarza w PR.
    assert "Znaleziska (22 z 22)" in markdown

    html = panel.get(f"{baza}?format=html")
    assert html.status_code == 200
    assert "<script" not in html.text
    assert html.text.count("znalezisko") >= 22


def test_eksport_jest_pobieraniem_a_nie_strona(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    response = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}/report?format=html")

    # Samodzielny HTML ma styl w treści; renderowanie go w originie panelu
    # kłóciłoby się z CSP i niepotrzebnie mieszałoby zaufanie.
    assert response.headers["content-disposition"].startswith("attachment;")


def test_nieznany_format_eksportu_jest_odrzucony(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    response = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}/report?format=pdf")
    assert response.status_code == 422


def test_nieznany_przebieg_i_projekt_daja_404(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    assert panel.get(f"/api/v1/projects/{project_id}/runs/nie-ma").status_code == 404
    assert panel.get("/api/v1/projects/424242/runs").status_code == 404


def test_raport_bez_znalezisk_nie_udaje_pomiaru(panel: Panel) -> None:
    project_id = panel.create_project()
    panel.import_sample("bez-znalezisk.json", project_id)
    body = panel.get(f"/api/v1/projects/{project_id}/runs/0000demo0003").json()

    coverage = next(g for g in body["report"]["gates"] if g["gate"] == "G2.diff_coverage")
    assert coverage["findings"] == []
    assert "coverage.diff_ratio" not in coverage["facts"]


def test_import_przez_api_bez_pliku_w_ciele_zadania(panel: Panel) -> None:
    project_id = panel.create_project()
    raw = json.dumps(load_sample("bez-znalezisk.json")).encode("utf-8")
    response = panel.client.post(
        "/api/v1/reports/import",
        content=raw,
        headers={"Origin": "http://127.0.0.1", "x-csrf-token": panel.token,
                 "content-type": "application/json"},
        params={"project_id": project_id},
    )
    # `project_id` jest polem formularza, nie parametrem zapytania — panel nie
    # zgaduje, do którego projektu należy raport.
    assert response.status_code == 422
