"""Granice zaufania HTTP — scenariusz odbioru 9 z `PLAN-WEB-UI.md` §9."""

from __future__ import annotations

import json

import pytest
from conftest import BASE_URL, Panel, load_sample
from fastapi.testclient import TestClient

from gatekeeper_web.app import create_app
from gatekeeper_web.config import Settings


def test_obcy_naglowek_host_jest_odrzucony(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="http://evil.example") as client:
        response = client.get("/")
    # DNS rebinding zamienia „tylko localhost" w panel publiczny, jeśli serwer
    # nie sprawdza, o jaki adres go poproszono.
    assert response.status_code == 421


def test_naglowki_bezpieczenstwa_sa_ustawione(panel: Panel) -> None:
    headers = panel.get("/").headers

    assert "default-src 'none'" in headers["content-security-policy"]
    assert "cdn" not in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"


def test_zapis_bez_tokenu_csrf_jest_odrzucony(panel: Panel) -> None:
    response = panel.client.post(
        "/projekty", data={"name": "Bez tokenu"}, headers={"Origin": BASE_URL}
    )
    assert response.status_code == 403
    assert "CSRF" in response.text


def test_zapis_z_obcego_originu_jest_odrzucony(panel: Panel) -> None:
    response = panel.client.post(
        "/projekty",
        data={"name": "Z obcej strony", "csrf_token": panel.token},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert "origin" in response.text


def test_ciasteczko_sesji_jest_httponly(panel: Panel) -> None:
    response = panel.get("/")
    ciasteczko = response.headers.get("set-cookie", "")
    if ciasteczko:
        assert "HttpOnly" in ciasteczko
        assert "SameSite=lax" in ciasteczko


def test_tresc_raportu_nie_wykonuje_sie_w_przegladarce(panel: Panel) -> None:
    zlosliwy = load_sample("bez-znalezisk.json")
    zlosliwy["gates"][0]["findings"] = [
        {
            "gate": "G0.scope",
            "rule_id": "demo.xss",
            "severity": "high",
            "title": "<script>alert('xss')</script>",
            "failure_scenario": "<img src=x onerror=alert(1)>",
            "file": "a.ts",
            "line": 1,
            "confidence": 1.0,
            "evidence": {"snippet": "</script><script>alert(2)</script>"},
            "fingerprint": "deadbeefdeadbeef",
        }
    ]
    project_id = panel.create_project()
    panel.import_bytes(json.dumps(zlosliwy).encode("utf-8"), project_id)

    strona = panel.get(f"/projekty/{project_id}/przebiegi/0000demo0003").text
    assert "<script>alert" not in strona
    assert "&lt;script&gt;alert" in strona

    eksport = panel.get(
        f"/api/v1/projects/{project_id}/runs/0000demo0003/report?format=html"
    ).text
    assert "<script>alert" not in eksport
    # Atrybut zdarzenia jest w treści jako tekst, a nie jako znacznik HTML.
    assert "<img src=x" not in eksport
    assert "&lt;img src=x onerror=alert(1)&gt;" in eksport


def test_identyfikator_przebiegu_nie_jest_sciezka(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    response = panel.get(f"/api/v1/projects/{project_id}/runs/..%2F..%2Fetc%2Fpasswd")
    # Artefakty i raporty pobiera się po identyfikatorze, nie po ścieżce
    # z parametru żądania (plan §8).
    assert response.status_code == 404


def test_nazwa_pobieranego_pliku_jest_oczyszczona(panel: Panel) -> None:
    raport = load_sample("bez-znalezisk.json")
    raport["run_id"] = 'zly"raport bez slasha'
    project_id = panel.create_project()
    panel.import_bytes(json.dumps(raport).encode("utf-8"), project_id)

    response = panel.get(
        f"/api/v1/projects/{project_id}/runs/{raport['run_id']}/report?format=json"
    )
    assert response.status_code == 200
    nazwa = response.headers["content-disposition"].split("filename=")[1].strip('"')
    assert '"' not in nazwa
    assert " " not in nazwa
    assert nazwa.endswith(".json")


def test_dokumentacja_interaktywna_jest_wylaczona(panel: Panel) -> None:
    assert panel.get("/docs").status_code == 404
    assert panel.get("/api/v1/openapi.json").status_code == 200


@pytest.mark.parametrize(
    ("metoda", "sciezka"),
    [
        ("post", "/api/v1/jobs"),
        ("post", "/api/v1/policies"),
        ("post", "/api/v1/projects/1/preview"),
        ("post", "/nowa-kontrola"),
        ("post", "/polityki"),
        ("post", "/zadania/1/anuluj"),
    ],
)
def test_operacje_zapisujace_wymagaja_tokenu_csrf(
    panel: Panel, metoda: str, sciezka: str
) -> None:
    """Uruchamianie narzędzi na cudzym kodzie tym bardziej nie może być
    wyzwalane przez formularz z obcej strony."""
    response = getattr(panel.client, metoda)(
        sciezka, json={}, headers={"Origin": BASE_URL}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "sciezka",
    ["/api/v1/jobs", "/api/v1/policies", "/nowa-kontrola", "/polityki"],
)
def test_operacje_zapisujace_odrzucaja_obcy_origin(panel: Panel, sciezka: str) -> None:
    response = panel.client.post(
        sciezka,
        json={},
        headers={"Origin": "http://evil.example", "x-csrf-token": panel.token},
    )
    assert response.status_code == 403


def test_wejscie_zadania_nie_wybiera_interpretera_ani_pluginow(
    panel: Panel, gotowy_projekt: int
) -> None:
    """API przyjmuje projekt i nazwę wersji Git — nie polecenie do wykonania."""
    response = panel.post_json(
        "/api/v1/jobs",
        {
            "project_id": gotowy_projekt,
            "base": "main",
            "repo_path": "/etc",
            "python": "/bin/sh",
            "policy_yaml": "version: 1\nblocking: []\n",
            "plugins": ["zly.plugin"],
        },
    )
    assert response.status_code == 202
    body = panel.get(f"/api/v1/jobs/{response.json()['job']['id']}").json()
    payload = panel.client.get(f"/api/v1/jobs/{response.json()['job']['id']}").json()

    # Nadmiarowe pola są ignorowane: zamrożone wejście bierze się z rejestru
    # projektu i z zatwierdzonego profilu polityki.
    assert body["input"]["policy_revision"] == 1
    assert "repo_path" not in payload["input"]
