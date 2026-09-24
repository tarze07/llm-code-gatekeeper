from __future__ import annotations

import sys

import pytest

from gatekeeper_core.adapters.base import ToolFailed, run_tool
from gatekeeper_core.core.runner import (
    Sandbox,
    SandboxPolicy,
    SandboxUnavailable,
    filesystem_isolation_available,
    network_isolation_available,
    scrub_environment,
)


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_sekrety_nie_trafiaja_do_uruchamianego_procesu(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_tajne")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "tajne")
    monkeypatch.setenv("BEZPIECZNA", "wartosc")

    result = Sandbox().run(
        python("import os; print(sorted(k for k in os.environ if 'TOKEN' in k or 'SECRET' in k))"),
        cwd=tmp_path,
    )

    assert result.ok
    assert result.stdout.strip() == "[]"


def test_keep_env_przywraca_wskazana_zmienna(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "potrzebny")
    sandbox = Sandbox(SandboxPolicy(keep_env=("TEST_API_TOKEN",)))

    result = sandbox.run(python("import os; print(os.environ.get('TEST_API_TOKEN'))"), cwd=tmp_path)

    assert result.stdout.strip() == "potrzebny"


def test_scrub_environment_jest_czysta_funkcja():
    env = {"PATH": "/bin", "NPM_TOKEN": "x", "DB_PASSWORD": "y", "HOME": "/h"}
    assert set(scrub_environment(env)) == {"PATH", "HOME"}
    assert set(scrub_environment(env, keep=("NPM_TOKEN",))) == {"PATH", "HOME", "NPM_TOKEN"}


@pytest.mark.skipif(not network_isolation_available(), reason="brak przestrzeni nazw użytkownika")
def test_domyslnie_proces_nie_ma_dostepu_do_sieci(tmp_path):
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=3)\n"
        "    print('SIEC')\n"
        "except OSError:\n"
        "    print('ODCIETA')\n"
    )
    result = Sandbox().run(python(code), cwd=tmp_path)

    assert result.stdout.strip() == "ODCIETA"
    assert result.isolation == "filesystem-network"


@pytest.mark.skipif(not network_isolation_available(), reason="brak przestrzeni nazw użytkownika")
def test_pliki_pozostaja_czytelne_mimo_izolacji(tmp_path):
    (tmp_path / "dane.txt").write_text("zawartość", encoding="utf-8")

    result = Sandbox().run(python("print(open('dane.txt').read())"), cwd=tmp_path)

    assert "zawartość" in result.stdout


def test_sieci_da_sie_zazadac_swiadomie(tmp_path):
    """Bramka odpytująca rejestr pakietów musi móc wyjść na zewnątrz."""
    result = Sandbox().run(python("print('ok')"), cwd=tmp_path, network=True)

    assert result.isolation == "filesystem"
    assert result.stdout.strip() == "ok"


def test_przekroczony_limit_czasu_zabija_caly_proces(tmp_path):
    result = Sandbox(SandboxPolicy(timeout_s=1.0)).run(
        python("import time; time.sleep(30)"), cwd=tmp_path
    )

    assert result.timed_out
    assert not result.ok
    assert result.duration_s < 10


def test_limit_pamieci_jest_egzekwowany(tmp_path):
    sandbox = Sandbox(SandboxPolicy(memory_mb=128))

    result = sandbox.run(python("x = bytearray(512 * 1024 * 1024)"), cwd=tmp_path)

    assert not result.ok  # MemoryError albo zabity przez limit


def test_brak_programu_jest_jawnym_bledem(tmp_path):
    with pytest.raises(SandboxUnavailable, match="nie znaleziono programu"):
        Sandbox().run(["na-pewno-nie-ma-takiego-programu"], cwd=tmp_path)


def test_wymagana_izolacja_przerywa_gdy_niedostepna(tmp_path, monkeypatch):
    monkeypatch.setattr("gatekeeper_core.core.runner.filesystem_isolation_available", lambda: False)
    sandbox = Sandbox(SandboxPolicy(require_isolation=True))

    with pytest.raises(SandboxUnavailable, match="brak izolacji Bubblewrap"):
        sandbox.run(python("print(1)"), cwd=tmp_path)


def test_brak_bubblewrap_nie_pozwala_na_nieizolowane_wykonanie(tmp_path, monkeypatch):
    monkeypatch.setattr("gatekeeper_core.core.runner.filesystem_isolation_available", lambda: False)
    marker = tmp_path / "executed"
    with pytest.raises(SandboxUnavailable, match="brak izolacji Bubblewrap"):
        Sandbox(SandboxPolicy(require_isolation=False)).run(
            python("from pathlib import Path; Path('executed').touch()"), cwd=tmp_path
        )
    assert not marker.exists()


@pytest.mark.skipif(not filesystem_isolation_available(), reason="Bubblewrap niedostępny")
def test_pliki_hosta_i_dowiazania_sa_niedostepne(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("private")
    (work / "escape").symlink_to(secret)
    result = Sandbox().run(python(
        "from pathlib import Path\n"
        "for p in (Path('../secret'), Path('escape')):\n"
        "    try: p.read_text(); raise AssertionError('read escaped')\n"
        "    except (FileNotFoundError, PermissionError): pass\n"
        "for p in (Path('../secret'), Path('escape')):\n"
        "    try: p.write_text('changed')\n"
        "    except (FileNotFoundError, PermissionError): pass\n"
        "Path('artifact').write_text('ok')\n"
    ), cwd=work)
    assert result.ok, result.stderr
    assert secret.read_text() == "private"
    assert (work / "artifact").read_text() == "ok"


@pytest.mark.skipif(not filesystem_isolation_available(), reason="Bubblewrap niedostępny")
def test_jawny_katalog_wejsciowy_jest_tylko_do_odczytu(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    data = tmp_path / "input"
    data.mkdir()
    (data / "value").write_text("input")
    sandbox = Sandbox(SandboxPolicy(read_only_paths=(data,)))
    result = sandbox.run(python(
        f"from pathlib import Path; p=Path({str(data / 'value')!r}); print(p.read_text()); "
        "p.write_text('changed')"
    ), cwd=work)
    assert not result.ok
    assert result.stdout.strip() == "input"
    assert (data / "value").read_text() == "input"


@pytest.mark.skipif(not filesystem_isolation_available(), reason="Bubblewrap niedostępny")
def test_timeout_zabija_takze_proces_w_nowej_sesji(tmp_path):
    result = Sandbox(SandboxPolicy(timeout_s=0.3)).run(python(
        "import os,time\n"
        "if os.fork() == 0:\n"
        "    os.setsid(); time.sleep(30)\n"
        "else: time.sleep(30)\n"
    ), cwd=tmp_path)
    assert result.timed_out
    assert result.duration_s < 3


@pytest.mark.skipif(not filesystem_isolation_available(), reason="Bubblewrap niedostępny")
def test_node_modules_nie_moze_udostepnic_dowolnego_katalogu_hosta(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "node_modules").symlink_to(tmp_path, True)
    with pytest.raises(SandboxUnavailable, match="niezaufanym dowiązaniem"):
        Sandbox().run(python("print('must not run')"), cwd=work)


def test_awaria_izolacji_nie_udaje_braku_opcjonalnego_narzedzia(tmp_path, monkeypatch):
    monkeypatch.setattr("gatekeeper_core.core.runner.filesystem_isolation_available", lambda: False)
    with pytest.raises(ToolFailed, match="brak izolacji Bubblewrap"):
        run_tool(python("print('must not run')"), tmp_path, Sandbox(), 1)
