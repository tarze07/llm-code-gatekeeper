"""Backend `container` bez silnika: składanie `docker run` i mapowanie ścieżek.

Testy z prawdziwym Dockerem są w `test_container_integration.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gatekeeper_core.core import container, runner
from gatekeeper_core.core.container import _Mount, _PathMap
from gatekeeper_core.core.runner import Sandbox, SandboxPolicy, SandboxUnavailable


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(container, "engine", lambda: "/usr/bin/docker")
    monkeypatch.setattr(container, "_image_present", lambda engine, name: True)
    monkeypatch.delenv(container.OWNER_ENV, raising=False)


def _command(tmp_path, argv, env=None, policy=None, network=False):
    work = tmp_path / "wt"
    work.mkdir(exist_ok=True)
    command, mapping = container.build_command(
        argv, work, env or {}, policy or SandboxPolicy(), network, (), "gk-test"
    )
    return work, command, mapping


def _option_values(command, option):
    return [command[i + 1] for i, arg in enumerate(command) if arg == option]


def test_polityka_izolacji_trafia_do_docker_run(fake_engine, tmp_path):
    work, command, _ = _command(tmp_path, ["ruff", "check", "."])

    assert command[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert _option_values(command, "--network") == ["none"]
    for flag in ("--read-only", "--init"):
        assert flag in command
    assert _option_values(command, "--cap-drop") == ["ALL"]
    assert _option_values(command, "--security-opt") == ["no-new-privileges"]
    assert _option_values(command, "--memory") == ["4096m"]
    assert _option_values(command, "--workdir") == ["/work"]
    assert f"type=bind,source={work},target=/work" in _option_values(command, "--mount")
    assert command[-3:] == ["ruff", "check", "."]


def test_siec_tylko_na_jawne_zadanie(fake_engine, tmp_path):
    _, command, _ = _command(tmp_path, ["pip-audit"], network=True)
    assert _option_values(command, "--network") == ["bridge"]


def test_baza_git_jest_tylko_do_odczytu(fake_engine, tmp_path):
    work = tmp_path / "wt"
    (work / ".git").mkdir(parents=True)
    _, command, _ = _command(tmp_path, ["git", "status"])
    assert f"type=bind,source={work / '.git'},target=/work/.git,readonly" in _option_values(
        command, "--mount"
    )


def test_sciezki_hosta_w_argumentach_sa_przepisywane(fake_engine, tmp_path):
    work = tmp_path / "wt"
    work.mkdir()
    readonly = tmp_path / "reguly"
    readonly.mkdir()
    policy = SandboxPolicy(read_only_paths=(readonly,))
    _, command, _ = _command(
        tmp_path,
        [sys.executable, "-m", "pytest", str(work / "tests" / "t.py"), f"--rules={readonly}/a.yml"],
        policy=policy,
    )

    assert command[-5:] == ["python", "-m", "pytest", "/work/tests/t.py", "--rules=/ro/0/a.yml"]
    assert f"type=bind,source={readonly},target=/ro/0,readonly" in _option_values(
        command, "--mount"
    )


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [("ruff", "ruff"), ("C:\\venv\\Scripts\\ruff.exe", "ruff"), ("npm.cmd", "npm"),
     ("/usr/bin/python3.12", "python")],
)
def test_program_hosta_to_nazwa_w_obrazie(fake_engine, tmp_path, argv0, expected):
    _, command, _ = _command(tmp_path, [argv0, "--version"])
    assert command[-2:] == [expected, "--version"]


def test_do_kontenera_idzie_tylko_srodowisko_wniesione_przez_wywolujacego(
    fake_engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOST_ONLY", "x")
    monkeypatch.setenv("ZACHOWAJ", "tak")
    work = tmp_path / "wt"
    work.mkdir()
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(work / "src"), str(work)])
    env["PATH"] = "/host/bin"
    policy = SandboxPolicy(keep_env=("ZACHOWAJ",))
    _, command, _ = _command(tmp_path, ["python"], env=env, policy=policy)

    values = _option_values(command, "--env")
    assert "PYTHONPATH=/work/src:/work" in values
    assert "ZACHOWAJ=tak" in values
    assert not any(v.startswith(("HOST_ONLY=", "PATH=")) for v in values)


def test_etykieta_wlasciciela_pozwala_sprzatac_kontenery_bramki(fake_engine, tmp_path, monkeypatch):
    monkeypatch.setenv(container.OWNER_ENV, "123-abc")
    _, command, _ = _command(tmp_path, ["ruff"])
    assert f"{container.OWNER_LABEL}=123-abc" in _option_values(command, "--label")


def test_mapa_sciezek_windows_w_obie_strony():
    mapping = _PathMap([_Mount(Path("C:\\Users\\u\\Temp\\gk\\wt"), "/work", False)])

    assert mapping.to_container("C:\\Users\\u\\Temp\\gk\\wt\\src\\a.py") == "/work/src/a.py"
    assert mapping.to_container("c:/users/u/temp/gk/wt/src/a.py") == "/work/src/a.py"
    assert mapping.to_host('{"file": "/work/src/a.py"}') == (
        '{"file": "C:/Users/u/Temp/gk/wt/src/a.py"}'
    )


def test_mapa_nie_rusza_sciezek_tylko_podobnych():
    mapping = _PathMap([_Mount(Path("/tmp/wt"), "/work", False)])

    assert mapping.to_host("/workshop/a.py") == "/workshop/a.py"
    assert mapping.to_host("/srv/work/a.py") == "/srv/work/a.py"
    assert mapping.to_container("/tmp/wtx/a.py") == "/tmp/wtx/a.py"
    assert mapping.to_host("/work/a.py:3:1") == "/tmp/wt/a.py:3:1"


def test_przecinek_w_sciezce_nie_rozbija_specyfikacji_mount():
    spec = container._bind(Path("/tmp/a,b"), "/work", True)
    assert spec == 'type=bind,"source=/tmp/a,b",target=/work,readonly'


def test_brak_silnika_to_brak_izolacji_nie_wykonanie(monkeypatch, tmp_path):
    monkeypatch.setenv(runner.BACKEND_ENV, "container")
    monkeypatch.setattr(container, "engine", lambda: None)

    with pytest.raises(SandboxUnavailable, match="silnika kontenerów"):
        Sandbox().run(["ruff", "--version"], cwd=tmp_path)
    assert not runner.isolation_available()
    assert "BRAK izolacji" in runner.describe_isolation()


def test_nieznany_backend_jest_bledem(monkeypatch, tmp_path):
    monkeypatch.setenv(runner.BACKEND_ENV, "chroot")
    with pytest.raises(SandboxUnavailable, match="nieznany backend"):
        Sandbox().run(["ruff"], cwd=tmp_path)
    assert not runner.isolation_available()


def test_domyslny_backend_zalezy_od_systemu(monkeypatch):
    monkeypatch.delenv(runner.BACKEND_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert runner.backend() == "container"
    monkeypatch.setattr(sys, "platform", "linux")
    assert runner.backend() == "bwrap"


def test_obraz_projektu_instaluje_wykryte_zaleznosci(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.csproj").write_text("<Project/>")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "Stary.csproj").write_text("<Project/>")

    text = container.project_dockerfile(tmp_path, base="gatekeeper-tools:1")

    assert text.startswith("# Obraz projektu")
    assert "FROM gatekeeper-tools:1" in text
    assert "RUN pip install -r requirements.txt" in text
    assert "RUN npm ci --no-audit --no-fund" in text
    assert "RUN dotnet restore src/App.csproj" in text
    assert "Stary.csproj" not in text
    assert text.rstrip().endswith("USER gatekeeper")


def test_obraz_projektu_bez_manifestow(tmp_path):
    text = container.project_dockerfile(tmp_path)
    assert "Nie wykryto manifestów" in text


def test_kontener_konczy_sie_sam_nawet_bez_nadzorcy(fake_engine, tmp_path):
    work = tmp_path / "wt"
    work.mkdir()
    command, _ = container.build_command(
        ["sleep", "999"], work, {}, SandboxPolicy(), False, (), "gk-t", timeout_s=60
    )
    image_at = command.index(container.image())
    assert command[image_at + 1:] == [
        "timeout", "-s", "KILL", str(60 + container.SELF_DESTRUCT_GRACE_S), "sleep", "999"
    ]
