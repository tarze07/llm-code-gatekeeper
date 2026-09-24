"""Tryb logowania panelu: domyślnie żaden, opcjonalnie kod startowy.

`serve` startuje bez sesji (panel lokalny, pętla zwrotna). Zestaw poniżej
pilnuje obu stron tego przełącznika: że domyślnie naprawdę nic nie pyta
i że `--wymagaj-logowania` wraca do sesji z planu §8.
"""

from __future__ import annotations

import pytest
from conftest import BASE_URL, ORIGIN
from fastapi.testclient import TestClient

from gatekeeper_web.app import create_app
from gatekeeper_web.auth import SESSION_COOKIE, generate_bootstrap_code, generate_session_secret
from gatekeeper_web.config import Settings


def _app(settings: Settings, *, code: str) -> TestClient:
    secret = generate_session_secret()
    locked = Settings(
        state_dir=settings.state_dir,
        allowed_repo_roots=settings.allowed_repo_roots,
        start_supervisor=False,
        require_login=True,
        session_secret=secret,
        bootstrap_code=code,
    )
    return TestClient(create_app(locked), base_url=BASE_URL, follow_redirects=False)


def test_bez_sesji_html_trafia_na_logowanie(settings: Settings) -> None:
    code = generate_bootstrap_code()
    with _app(settings, code=code) as client:
        response = client.get("/")
    assert response.status_code == 303
    assert "/logowanie" in response.headers["location"]
    assert code not in response.headers["location"]
    assert SESSION_COOKIE not in response.headers.get("location", "")


def test_bez_sesji_api_odmawia(settings: Settings) -> None:
    with _app(settings, code=generate_bootstrap_code()) as client:
        response = client.get("/api/v1/projects")
    assert response.status_code == 401
    assert "kodem startowym" in response.json()["detail"]


def test_statyczne_zasoby_sa_publiczne(settings: Settings) -> None:
    with _app(settings, code=generate_bootstrap_code()) as client:
        response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_zly_kod_nie_zaklada_sesji(settings: Settings) -> None:
    code = generate_bootstrap_code()
    with _app(settings, code=code) as client:
        client.get("/logowanie")
        response = client.post(
            "/logowanie",
            data={"csrf_token": client.cookies["gk_csrf"], "code": "0000-0000-0000"},
            headers=ORIGIN,
        )
    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies
    assert "Niepoprawny" in response.text


def test_kod_zaklada_sesje_httponly_i_nie_trafia_do_url(settings: Settings) -> None:
    code = generate_bootstrap_code()
    with _app(settings, code=code) as client:
        client.get("/logowanie")
        response = client.post(
            "/logowanie",
            data={"csrf_token": client.cookies["gk_csrf"], "code": code, "nastepny": "/projekty"},
            headers=ORIGIN,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/projekty"
        assert code not in response.headers["location"]
        ciasteczko = response.headers.get("set-cookie", "")
        assert SESSION_COOKIE in ciasteczko
        assert "HttpOnly" in ciasteczko
        assert "SameSite=lax" in ciasteczko
        assert code not in ciasteczko

        inside = client.get("/")
        assert inside.status_code == 200
        assert "Pulpit" in inside.text or "Zacznij od projektu" in inside.text


def test_kod_jest_jednorazowy(settings: Settings) -> None:
    code = generate_bootstrap_code()
    with _app(settings, code=code) as client:
        client.get("/logowanie")
        token = client.cookies["gk_csrf"]
        first = client.post(
            "/logowanie",
            data={"csrf_token": token, "code": code},
            headers=ORIGIN,
        )
        assert first.status_code == 303
        client.cookies.delete(SESSION_COOKIE)
        second = client.post(
            "/logowanie",
            data={"csrf_token": token, "code": code},
            headers=ORIGIN,
        )
    assert second.status_code == 403


def test_nastepny_nie_jest_otwartym_przekierowaniem(settings: Settings) -> None:
    code = generate_bootstrap_code()
    with _app(settings, code=code) as client:
        client.get("/logowanie")
        response = client.post(
            "/logowanie",
            data={
                "csrf_token": client.cookies["gk_csrf"],
                "code": code,
                "nastepny": "https://evil.example/steal",
            },
            headers=ORIGIN,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ------------------------------------------------- tryb domyślny: bez sesji


def _otwarty(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url=BASE_URL, follow_redirects=False)


def test_domyslnie_panel_nie_pyta_o_kod(settings: Settings) -> None:
    """Fixture `settings` używa domyślnego `require_login`, czyli False."""
    assert settings.require_login is False
    with _otwarty(settings) as client:
        strona = client.get("/")
        api = client.get("/api/v1/projects")
    assert strona.status_code == 200
    assert api.status_code == 200


def test_bez_logowania_strona_kodu_odsyla_na_pulpit(settings: Settings) -> None:
    with _otwarty(settings) as client:
        response = client.get("/logowanie")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_bez_logowania_nie_ma_ciasteczka_sesji(settings: Settings) -> None:
    """Brak sesji to brak sesji — panel nie zakłada jej po cichu."""
    with _otwarty(settings) as client:
        response = client.get("/")
    assert SESSION_COOKIE not in response.cookies
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_tryb_logowania_przechodzi_przez_srodowisko(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--reload` odtwarza ustawienia z env; zgubiony klucz otwierałby panel."""
    monkeypatch.delenv("GATEKEEPER_WEB_REQUIRE_LOGIN", raising=False)
    assert Settings.from_env().require_login is False
    monkeypatch.setenv("GATEKEEPER_WEB_REQUIRE_LOGIN", "1")
    assert Settings.from_env().require_login is True
    monkeypatch.setenv("GATEKEEPER_WEB_REQUIRE_LOGIN", "0")
    assert Settings.from_env().require_login is False
