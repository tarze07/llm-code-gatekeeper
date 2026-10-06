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
    # Nie `no-referrer`: ta wartość każe przeglądarce wysyłać `Origin: null`
    # przy każdym zapisie i wyłączała wszystkie formularze panelu.
    # Szczegóły przy `test_polityka_referrera_nie_ukrywa_originu`.
    assert headers["referrer-policy"] == "same-origin"
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


# ----------------------------------------------- Origin: ten sam panel vs obcy
#
# Kontrola `Origin` porównywała gołe napisy, więc operator z `localhost` w pasku
# adresu dostawał 403 „żądanie z obcego origin" przy każdym zapisie, mimo że
# rozmawiał z własnym panelem. Poniższe testy trzymają obie strony granicy:
# zapis z tego samego panelu ma przechodzić, wszystko inne — nie.


@pytest.mark.parametrize(
    "origin, host",
    [
        ("http://localhost:8080", "127.0.0.1:8080"),
        ("http://127.0.0.1:8080", "localhost:8080"),
        ("http://LocalHost:8080", "localhost:8080"),
        ("http://[::1]:8080", "[::1]:8080"),
    ],
)
def test_ten_sam_panel_innym_zapisem_adresu_przechodzi(origin: str, host: str) -> None:
    from gatekeeper_web.config import DEFAULT_ALLOWED_HOSTS
    from gatekeeper_web.security import origin_allowed

    assert origin_allowed(origin, host, DEFAULT_ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "origin, host, powod",
    [
        ("http://localhost:3000", "localhost:8080", "inna usługa na tej samej maszynie"),
        ("http://zly.example:8080", "localhost:8080", "cudza domena, ten sam port"),
        ("http://localhost.zly.example:8080", "localhost:8080", "nazwa udająca localhost"),
        ("https://abc-8080.devtunnels.ms", "localhost:8080", "tunel z przepisanym Host"),
        ("null", "localhost:8080", "piaskownica albo file://"),
        ("", "localhost:8080", "pusty nagłówek"),
    ],
)
def test_obcy_origin_nadal_odrzucony(origin: str, host: str, powod: str) -> None:
    from gatekeeper_web.config import DEFAULT_ALLOWED_HOSTS
    from gatekeeper_web.security import origin_allowed

    assert not origin_allowed(origin, host, DEFAULT_ALLOWED_HOSTS), powod


def test_odmowa_nazywa_oba_naglowki(panel: Panel) -> None:
    """Sam komunikat „obcy origin" nie pozwalał zdiagnozować własnego panelu."""
    response = panel.client.post(
        "/projekty",
        data={"name": "Z obcej strony", "csrf_token": panel.token},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert "evil.example" in response.text
    assert "Origin" in response.text


def test_polityka_referrera_nie_ukrywa_originu() -> None:
    """Nagłówek panelu nie może wyłączać jego własnych formularzy.

    `Referrer-Policy: no-referrer` każe przeglądarce wysłać `Origin: null`
    przy każdym POST (Fetch, „append a request `Origin` header"). Panel
    odrzuca `null`, więc sam sobie blokował każdy zapis z HTML-a — sprawdzone
    w Chrome: `no-referrer` → `Origin: null`, `same-origin` → prawdziwy adres.

    Ten test jest tu dlatego, że reszta zestawu tego nie złapie: `TestClient`
    podaje `Origin` jawnie i nigdy nie odtworzy zachowania przeglądarki.
    """
    from gatekeeper_web.security import REFERRER_POLICIES_HIDING_ORIGIN, SECURITY_HEADERS

    assert SECURITY_HEADERS["Referrer-Policy"] not in REFERRER_POLICIES_HIDING_ORIGIN


def test_odpowiedzi_panelu_nie_ukrywaja_originu(panel: Panel) -> None:
    """To samo na żywej odpowiedzi — nagłówek jedzie z każdej strony."""
    from gatekeeper_web.security import REFERRER_POLICIES_HIDING_ORIGIN

    polityka = panel.get("/projekty").headers.get("referrer-policy")
    assert polityka is not None
    assert polityka not in REFERRER_POLICIES_HIDING_ORIGIN


def test_origin_null_ma_wlasna_diagnoze(panel: Panel) -> None:
    """`null` to inna sytuacja niż cudza domena — komunikat ma to rozróżniać."""
    response = panel.client.post(
        "/polityki",
        data={"name": "Z piaskownicy", "csrf_token": panel.token},
        headers={"Origin": "null"},
    )
    assert response.status_code == 403
    assert "Origin: null" in response.text
