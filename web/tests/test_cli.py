"""CLI panelu: import bez przeglądarki i odmowa wystawienia na świat."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import SAMPLES
from gatekeeper_core.core.winjob import Job, process_alive
from typer.testing import CliRunner

from gatekeeper_web.cli import _start_supervisor, _stop_supervisor, app
from gatekeeper_web.config import Settings
from gatekeeper_web.jobs.supervisor import supervisor_running

runner = CliRunner()

WINDOWS = sys.platform == "win32"


def _uruchom_drzewo(
    polecenie: list[str], **kwargs: Any
) -> tuple[subprocess.Popen[bytes], Job | None]:
    """Proces z całym drzewem pod kontrolą testu.

    POSIX: własna sesja, sprzątana sygnałem grupy. Windows: Job Object —
    zakończenie joba zabija `serve`, nadzorcę i wszystko, co uruchomili.
    """
    if not WINDOWS:
        return (
            subprocess.Popen(polecenie, stderr=subprocess.STDOUT, start_new_session=True, **kwargs),
            None,
        )
    drzewo = Job()
    process = subprocess.Popen(
        polecenie,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined,unused-ignore]
        **kwargs,
    )
    drzewo.assign(process.pid)
    return process, drzewo


def _zatrzymaj_drzewo(process: subprocess.Popen[bytes], drzewo: Job | None) -> None:
    if drzewo is not None:
        try:
            drzewo.terminate()
            process.wait(timeout=10)
        finally:
            drzewo.close()
        return
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)  # type: ignore[attr-defined,unused-ignore]
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined,unused-ignore]
        process.wait(timeout=5)


def _wolny_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port: int = listener.getsockname()[1]
    return port


def _czekaj(warunek: Callable[[], bool], limit_s: float, opis: str) -> None:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if warunek():
            return
        time.sleep(0.1)
    raise AssertionError(opis)


def _zyje(pid: int) -> bool:
    if WINDOWS:
        return process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover
        return True
    # Zombie po zabitym nadzorcy (rodzic `serve` też nie żyje) — sprząta init.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


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
    port = _wolny_port()
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
        process, drzewo = _uruchom_drzewo(polecenie, env=env, stdout=log)
        try:
            # Krótki limit obowiązuje **tylko** sondę startową: dopóki panel
            # wstaje, przeterminowanie jest sygnałem „jeszcze nie", więc ma być
            # tanie. Żądania po pętli to już zwykłe zapytania do działającego
            # serwera — pierwsze wyrenderowanie pulpitu pod `--reload` potrafi
            # przekroczyć pół sekundy i wywracało test na wolniejszej maszynie.
            with httpx.Client(
                base_url=base, trust_env=False, timeout=10, follow_redirects=False
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, _log(logfile)
                    try:
                        response = client.get("/logowanie", timeout=0.5)
                    except httpx.TransportError:
                        time.sleep(0.05)
                        continue
                    break
                else:
                    raise AssertionError(f"panel nie wystartował: {_log(logfile)}")

                log = _log(logfile)
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
                assert "Started reloader process" in _log(logfile)
        finally:
            _zatrzymaj_drzewo(process, drzewo)




def _log(logfile: Path) -> str:
    # Panel pisze UTF-8 także na Windows (`utf8_console`); strona kodowa
    # systemu wywracałaby odczyt na polskich znakach.
    return logfile.read_text(encoding="utf-8", errors="replace")


def _pid_nadzorcy(settings: Settings) -> int:
    """PID z pliku blokady (`host:pid`) — nadzorca zapisuje go po przejęciu."""
    tresc = settings.supervisor_lock_path.read_text(encoding="utf-8").strip()
    return int(tresc.rsplit(":", 1)[1])


def test_serve_zatrzymuje_nadzorce_lagodnie(tmp_path: Path) -> None:
    """Zatrzymanie `serve` daje nadzorcy czas na rozliczenie i zdjęcie blokady.

    Na Windows SIGTERM to twarde TerminateProcess, więc łagodna ścieżka idzie
    przez plik stopu — kod 0 znaczy, że nadzorca wyszedł sam, z `shutdown()`.
    """
    settings = Settings(state_dir=tmp_path / "stan")
    settings.ensure_state_dir()
    nadzorca = _start_supervisor(settings)
    assert nadzorca is not None
    try:
        _czekaj(lambda: supervisor_running(settings), 30, "nadzorca nie wziął blokady")
        assert _start_supervisor(settings) is None, "drugi nadzorca na tym samym stanie"
    finally:
        _stop_supervisor(nadzorca)

    assert nadzorca.process.returncode == 0
    assert not supervisor_running(settings)
    if nadzorca.stop_file is not None:
        assert not nadzorca.stop_file.exists()


@pytest.mark.skipif(not WINDOWS, reason="Job Object serwera to mechanizm Windows")
def test_zabicie_serve_zabija_nadzorce(tmp_path: Path) -> None:
    """Twarde zabicie `serve` nie zostawia nadzorcy-sieroty.

    POSIX tego nie gwarantuje (nadzorca ma własną sesję, sprzątanie idzie
    sygnałem); na Windows jedyny uchwyt joba nadzorcy trzyma `serve`.
    """
    state = tmp_path / "stan"
    settings = Settings(state_dir=state)
    logfile = tmp_path / "server.log"
    polecenie = [
        sys.executable, "-m", "gatekeeper_web.cli", "serve",
        "--host", "127.0.0.1", "--port", str(_wolny_port()), "--state-dir", str(state),
    ]
    with logfile.open("w") as log:
        process, drzewo = _uruchom_drzewo(polecenie, stdout=log)
        try:
            _czekaj(lambda: supervisor_running(settings), 30, _log(logfile))
            pid = _pid_nadzorcy(settings)
            assert _zyje(pid)

            process.kill()  # TerminateProcess: `serve` nie wykona już nic
            process.wait(timeout=10)

            _czekaj(lambda: not _zyje(pid), 10, "nadzorca przeżył zabicie serve")
            _czekaj(lambda: not supervisor_running(settings), 10, "blokada nadzorcy została")
        finally:
            _zatrzymaj_drzewo(process, drzewo)


@pytest.mark.skipif(not WINDOWS, reason="strona kodowa konsoli to problem Windows")
def test_serve_wypisuje_polskie_znaki_bez_awarii(tmp_path: Path) -> None:
    """Konsola Windows w stronie kodowej bez „ł" nie może wywrócić CLI."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    wynik = subprocess.run(
        [sys.executable, "-m", "gatekeeper_web.cli", "serve", "--host", "0.0.0.0",
         "--state-dir", str(tmp_path)],
        capture_output=True, env=env, timeout=60, check=False,
    )
    tekst = (wynik.stdout + wynik.stderr).decode("utf-8", errors="replace")
    assert wynik.returncode == 3, tekst
    assert "odmowa startu" in tekst
    assert "zespołowa" in tekst


def test_srodowisko_panelu_trafia_do_path(monkeypatch) -> None:
    """Narzędzia bramek leżą obok interpretera panelu, nie w PATH systemu.

    `…/.venv/bin/gatekeeper-web` nie dokłada swojego katalogu do `PATH` —
    robi to dopiero `activate`. Bez tego `semgrep` i `diff-cover`,
    zainstalowane razem z panelem, były dla `shutil.which` niewidoczne.
    """
    import sysconfig

    from gatekeeper_web.cli import ensure_tools_on_path

    bindir = sysconfig.get_path("scripts")
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))

    wynik = ensure_tools_on_path()

    assert wynik.split(os.pathsep)[0] == bindir, "własne środowisko przed systemowym"
    assert "/usr/bin" in wynik.split(os.pathsep)


def test_dokladanie_do_path_jest_idempotentne(monkeypatch) -> None:
    import sysconfig

    from gatekeeper_web.cli import ensure_tools_on_path

    bindir = sysconfig.get_path("scripts")
    monkeypatch.setenv("PATH", os.pathsep.join([bindir, "/usr/bin"]))

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

    parts = ensure_tools_on_path().split(os.pathsep)

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
    if WINDOWS:
        # `shutil.which` na Windows szuka po PATHEXT — wystarczy skrypt .cmd.
        (korzen / "dotnet.cmd").write_text("@exit /b 0\r\n", encoding="ascii")
    else:
        (korzen / "dotnet").write_text("#!/bin/sh\n")
        (korzen / "dotnet").chmod(0o755)
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    monkeypatch.setenv("PATH", str(korzen))

    assert ensure_dotnet_root() == str(korzen.resolve())


def test_dotnet_root_operatora_zostaje(monkeypatch) -> None:
    from gatekeeper_web.cli import ensure_dotnet_root

    monkeypatch.setenv("DOTNET_ROOT", "/wybor/operatora")

    assert ensure_dotnet_root() == "/wybor/operatora"
