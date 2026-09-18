"""CLI panelu: import bez przeglądarki i odmowa wystawienia na świat."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import SAMPLES
from typer.testing import CliRunner

from gatekeeper_web.cli import app

runner = CliRunner()


def test_import_zaklada_projekt_na_zadanie(tmp_path: Path) -> None:
    wynik = runner.invoke(
        app,
        ["import", str(SAMPLES / "demo-celowe-usterki.json"), "--project", "Demo",
         "--state-dir", str(tmp_path), "--create"],
    )
    assert wynik.exit_code == 0, wynik.output
    assert "Zaimportowano przebieg 35e102d0d126" in wynik.output
    assert "22 znalezisk" in wynik.output


def test_import_do_nieznanego_projektu_wymaga_zgody(tmp_path: Path) -> None:
    wynik = runner.invoke(
        app,
        ["import", str(SAMPLES / "bez-znalezisk.json"), "--project", "Nie ma",
         "--state-dir", str(tmp_path)],
    )
    assert wynik.exit_code == 3
    assert "--create" in wynik.output


def test_powtorny_import_z_cli_nie_tworzy_duplikatu(tmp_path: Path) -> None:
    argumenty = [
        "import", str(SAMPLES / "bez-znalezisk.json"), "--project", "Demo",
        "--state-dir", str(tmp_path), "--create",
    ]
    runner.invoke(app, argumenty)
    wynik = runner.invoke(app, argumenty)

    assert wynik.exit_code == 0
    assert "nie utworzono duplikatu" in wynik.output


def test_uszkodzony_raport_konczy_sie_bledem(tmp_path: Path) -> None:
    zly = tmp_path / "zly.json"
    zly.write_text("{nie json", encoding="utf-8")
    wynik = runner.invoke(
        app, ["import", str(zly), "--project", "Demo", "--state-dir", str(tmp_path), "--create"]
    )
    assert wynik.exit_code == 1
    assert "raport odrzucony" in wynik.output


def test_serve_odmawia_nasluchu_poza_petla_zwrotna(tmp_path: Path) -> None:
    wynik = runner.invoke(
        app, ["serve", "--host", "0.0.0.0", "--state-dir", str(tmp_path)]
    )
    # Jednorazowy kod startowy nie zastępuje modelu zespołowego (plan §10).
    assert wynik.exit_code == 3
    assert "odmowa startu" in wynik.output


@pytest.mark.parametrize("wymagaj_logowania", [False, True])
def test_serve_reload_startuje_z_wybranym_katalogiem_stanu(
    tmp_path: Path, wymagaj_logowania: bool
) -> None:
    """Proces po przeładowaniu musi odtworzyć katalog stanu **i** tryb logowania.

    Oba przebiegi są tu potrzebne: `--reload` przekazuje ustawienia przez
    środowisko, więc zgubiony `GATEKEEPER_WEB_REQUIRE_LOGIN` otwierałby panel
    bez śladu w logu — dokładnie ta awaria, której nie widać z zewnątrz.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    state = tmp_path / "wybrany stan"
    ignored_state = tmp_path / "stan ze srodowiska"
    env = {**os.environ, "GATEKEEPER_WEB_STATE_DIR": str(ignored_state)}
    base = f"http://127.0.0.1:{port}"
    logfile = tmp_path / "server.log"
    polecenie = [
        sys.executable, "-m", "gatekeeper_web.cli", "serve", "--reload",
        "--host", "127.0.0.1", "--port", str(port), "--state-dir", str(state),
    ]
    if wymagaj_logowania:
        polecenie.append("--wymagaj-logowania")
    with logfile.open("w") as log:
        process = subprocess.Popen(
            polecenie, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            with httpx.Client(
                base_url=base, trust_env=False, timeout=0.5, follow_redirects=False
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, logfile.read_text()
                    try:
                        response = client.get("/logowanie")
                    except httpx.TransportError:
                        time.sleep(0.05)
                        continue
                    break
                else:
                    raise AssertionError(f"panel nie wystartował: {logfile.read_text()}")

                log = logfile.read_text()
                marker = "kod startowy (jednorazowy): "
                if wymagaj_logowania:
                    assert response.status_code == 200, response.text
                    assert marker in log, log
                    code = log.split(marker, 1)[1].splitlines()[0].strip()
                    logged = client.post(
                        "/logowanie",
                        data={"csrf_token": client.cookies["gk_csrf"], "code": code},
                        headers={"Origin": base},
                    )
                    assert logged.status_code == 303, logged.text
                else:
                    # Bez logowania strona kodu nie ma czego pytać i odsyła na pulpit.
                    assert response.status_code == 303, response.text
                    assert response.headers["location"] == "/"
                    assert marker not in log, log
                    client.get("/")

                created = client.post(
                    "/api/v1/projects", json={"name": "Reload"},
                    headers={"Origin": base, "x-csrf-token": client.cookies["gk_csrf"]},
                )
                assert created.status_code == 201, created.text
                assert (state / "panel.db").is_file()
                assert not ignored_state.exists()
                assert "Started reloader process" in logfile.read_text()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_srodowisko_panelu_trafia_do_path(monkeypatch) -> None:
    """Narzędzia bramek leżą obok interpretera panelu, nie w PATH systemu.

    `…/.venv/bin/gatekeeper-web` nie dokłada swojego katalogu do `PATH` —
    robi to dopiero `activate`. Bez tego `semgrep` i `diff-cover`,
    zainstalowane razem z panelem, były dla `shutil.which` niewidoczne.
    """
    import sysconfig

    from gatekeeper_web.cli import ensure_tools_on_path

    bindir = sysconfig.get_path("scripts")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    wynik = ensure_tools_on_path()

    assert wynik.split(":")[0] == bindir, "własne środowisko przed systemowym"
    assert "/usr/bin" in wynik.split(":")


def test_dokladanie_do_path_jest_idempotentne(monkeypatch) -> None:
    import sysconfig

    from gatekeeper_web.cli import ensure_tools_on_path

    bindir = sysconfig.get_path("scripts")
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin")

    assert ensure_tools_on_path().count(bindir) == 1


def test_globalne_narzedzia_dotnet_trafiaja_do_path(monkeypatch, tmp_path) -> None:
    """`gatekeeper-cs-helper` instaluje się do `~/.dotnet/tools`, a `dotnet tool
    install` zostawia dopisanie tego katalogu operatorowi. Trzy bramki padały
    więc na „nie znaleziono programu" na maszynie, gdzie narzędzie było."""
    from gatekeeper_web.cli import ensure_tools_on_path

    narzedzia = tmp_path / ".dotnet" / "tools"
    narzedzia.mkdir(parents=True)
    monkeypatch.setenv("DOTNET_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")

    parts = ensure_tools_on_path().split(":")

    assert str(narzedzia) in parts
    # Na końcu: narzędzie systemowe o tej samej nazwie ma pierwszeństwo.
    assert parts.index(str(narzedzia)) > parts.index("/usr/bin")


def test_nieistniejacy_katalog_narzedzi_dotnet_nie_smieci_w_path(
    monkeypatch, tmp_path
) -> None:
    from gatekeeper_web.cli import ensure_tools_on_path

    monkeypatch.setenv("DOTNET_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")

    assert "dotnet" not in ensure_tools_on_path()


def test_dotnet_root_wskazuje_instalacje_uzytkownika(monkeypatch, tmp_path) -> None:
    """Bez `DOTNET_ROOT` shim narzędzia nie znajduje .NET rozpakowanego do `~/.dotnet`."""
    from gatekeeper_web.cli import ensure_dotnet_root

    korzen = tmp_path / ".dotnet"
    korzen.mkdir()
    (korzen / "dotnet").write_text("#!/bin/sh\n")
    (korzen / "dotnet").chmod(0o755)
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    monkeypatch.setenv("PATH", str(korzen))

    assert ensure_dotnet_root() == str(korzen)


def test_dotnet_root_operatora_zostaje(monkeypatch) -> None:
    from gatekeeper_web.cli import ensure_dotnet_root

    monkeypatch.setenv("DOTNET_ROOT", "/wybor/operatora")

    assert ensure_dotnet_root() == "/wybor/operatora"
