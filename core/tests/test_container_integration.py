"""Backend `container` z prawdziwym silnikiem (Docker/Podman) i obrazem narzędzi.

Pomijane, gdy silnika albo obrazu `gatekeeper-tools` nie ma — tak jak testy
Bubblewrap bez bwrap. Obraz: `docker build -t gatekeeper-tools:latest
-f container/Dockerfile .`
"""

from __future__ import annotations

import subprocess
import time
import uuid

import pytest

from gatekeeper_core.core import container, runner
from gatekeeper_core.core.runner import (
    ExecutableUnavailable,
    Sandbox,
    SandboxPolicy,
)

pytestmark = pytest.mark.skipif(
    not container.container_isolation_available(),
    reason="brak silnika kontenerów albo obrazu gatekeeper-tools",
)


@pytest.fixture(autouse=True)
def container_backend(monkeypatch):
    monkeypatch.setenv(runner.BACKEND_ENV, "container")


def _py(code: str) -> list[str]:
    return ["python", "-c", code]


def _containers(label: str) -> list[str]:
    engine = container.engine()
    assert engine
    listed = subprocess.run(
        [engine, "ps", "-aq", "--filter", f"label={label}"],
        capture_output=True, text=True, check=True,
    )
    return listed.stdout.split()


def test_kod_pisze_do_kopii_a_wynik_wraca_ze_sciezka_hosta(tmp_path):
    result = Sandbox().run(
        _py("import pathlib; p = pathlib.Path('wynik.txt'); p.write_text('ok');"
            " print(p.resolve())"),
        cwd=tmp_path,
    )

    assert result.ok, result.stderr
    assert result.isolation == "container"
    assert (tmp_path / "wynik.txt").read_text() == "ok"
    assert result.stdout.strip() == f"{tmp_path.resolve()}/wynik.txt"


def test_system_plikow_poza_kopia_jest_tylko_do_odczytu(tmp_path):
    result = Sandbox().run(_py("open('/usr/zly', 'w')"), cwd=tmp_path)
    assert result.returncode != 0
    assert "Read-only file system" in result.stderr


def test_domyslnie_brak_sieci(tmp_path):
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('SIEC')\n"
        "except OSError:\n"
        "    print('BRAK')\n"
    )
    result = Sandbox().run(_py(code), cwd=tmp_path)
    assert result.stdout.strip() == "BRAK"


def test_sekrety_hosta_nie_trafiaja_do_kontenera(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_sekret")
    monkeypatch.setenv("ZWYKLA", "x")
    code = "import os; print(os.environ.get('GITHUB_TOKEN'), os.environ.get('ZWYKLA'))"

    result = Sandbox().run(_py(code), cwd=tmp_path)
    # ZWYKLA jest środowiskiem hosta, nie dodatkiem wywołującego — też nie idzie.
    assert result.stdout.strip() == "None None"

    kept = Sandbox(SandboxPolicy(keep_env=("ZWYKLA",))).run(_py(code), cwd=tmp_path)
    assert kept.stdout.strip() == "None x"


def test_nie_dziala_jako_root(tmp_path):
    result = Sandbox().run(_py("import os; print(os.getuid())"), cwd=tmp_path)
    assert result.stdout.strip() != "0"


def test_timeout_usuwa_kontener(tmp_path):
    started = time.monotonic()
    result = Sandbox().run(["sleep", "120"], cwd=tmp_path, timeout_s=3)

    assert result.timed_out
    assert time.monotonic() - started < 60
    names = [arg for i, arg in enumerate(result.command) if result.command[i - 1] == "--name"]
    engine = container.engine()
    assert engine
    left = subprocess.run(
        [engine, "ps", "-aq", "--filter", f"name={names[0]}"],
        capture_output=True, text=True, check=True,
    )
    assert left.stdout.strip() == ""


def test_brak_programu_w_obrazie_to_brak_narzedzia(tmp_path):
    with pytest.raises(ExecutableUnavailable):
        Sandbox().run(["nie-ma-takiego-narzedzia"], cwd=tmp_path)


def test_sprzatanie_po_etykiecie_wlasciciela(tmp_path, monkeypatch):
    owner = f"test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(container.OWNER_ENV, owner)
    engine = container.engine()
    assert engine
    # Kontener, którego klient zniknął (jak po zabiciu workera bramki).
    command, _ = container.build_command(
        ["sleep", "120"], tmp_path, {}, SandboxPolicy(), False, (), f"gk-{owner}"
    )
    detached = [command[0], "run", "-d", *command[3:]]
    subprocess.run(detached, capture_output=True, check=True)
    assert _containers(f"{container.OWNER_LABEL}={owner}")

    container.remove_containers(owner=owner)
    assert _containers(f"{container.OWNER_LABEL}={owner}") == []


@pytest.mark.parametrize(
    "argv",
    [
        ["ruff", "--version"],
        ["mypy", "--version"],
        ["semgrep", "--version"],
        ["gitleaks", "version"],
        ["pip-audit", "--version"],
        ["diff-cover", "--version"],
        ["node", "--version"],
        ["dotnet", "--version"],
        ["gatekeeper-cs-helper", "--help"],
    ],
)
def test_obraz_ma_narzedzia_bramy(tmp_path, argv):
    result = Sandbox(SandboxPolicy(timeout_s=120)).run(argv, cwd=tmp_path)
    assert not result.timed_out
    output = (result.stdout + result.stderr).strip()
    # Helper C# bez polecenia wypisuje składnię i kończy się kodem 2 — to też
    # dowód, że jest w obrazie i uruchamia się pod dotnet.
    assert result.returncode == 0 or "użycie: gatekeeper-cs-helper" in output, output
    assert output


def test_home_i_tmp_sa_zapisywalne_dla_uzytkownika(tmp_path):
    code = (
        "import os, pathlib; h = pathlib.Path(os.environ['HOME']);"
        " (h / '.cache').mkdir(); pathlib.Path('/tmp/x').write_text('1'); print('OK')"
    )
    result = Sandbox().run(_py(code), cwd=tmp_path)
    assert result.stdout.strip() == "OK", result.stderr
