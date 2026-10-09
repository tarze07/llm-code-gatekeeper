"""Ekran środowiska zależny od backendu izolacji (bwrap / kontener)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from gatekeeper_core.core import container, runner

from gatekeeper_web import config
from gatekeeper_web.config import Settings
from gatekeeper_web.services import environment as env_mod


@pytest.fixture
def kontener(monkeypatch):
    monkeypatch.setenv(runner.BACKEND_ENV, "container")
    monkeypatch.setattr(container, "engine", lambda: "/usr/bin/docker")
    monkeypatch.setattr(container, "_image_present", lambda engine, name: True)
    monkeypatch.setattr(env_mod.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(env_mod, "_version_of", lambda path, args: "1.0")
    monkeypatch.setattr(env_mod, "_probe_cache", None)


@pytest.mark.skipif(sys.platform == "win32", reason="Bubblewrap istnieje tylko na Linuksie")
def test_bwrap_bez_zmian(monkeypatch) -> None:
    monkeypatch.setenv(runner.BACKEND_ENV, "bwrap")
    monkeypatch.setattr(runner, "filesystem_isolation_available", lambda: False)
    monkeypatch.setattr(env_mod, "_probe_cache", None)
    env = env_mod.collect()
    assert env.backend == "bwrap"
    assert any(t.name == "bwrap" for t in env.tools)
    assert not any(t.in_image for t in env.tools)
    assert not env.isolation_available
    assert any("apt-get install bubblewrap" in b for b in env.blockers)


def test_kontener_z_obrazem_moze_uruchamiac(kontener) -> None:
    env = env_mod.collect()
    assert env.backend == "container"
    assert env.engine_path == "/usr/bin/docker"
    assert env.image_name == container.image()
    assert env.image_present
    assert env.can_run and env.blockers == []
    by_name = {t.name: t for t in env.tools}
    assert "bwrap" not in by_name
    for name in ("semgrep", "gitleaks", "node", "dotnet"):
        assert by_name[name].in_image and by_name[name].available
    assert not by_name["git"].in_image


def test_kontener_bez_silnika_na_windows(kontener, monkeypatch) -> None:
    monkeypatch.setattr(container, "engine", lambda: None)
    monkeypatch.setattr(env_mod.sys, "platform", "win32")
    env = env_mod.collect()
    assert not env.can_run and not env.isolation_available
    text = " ".join(env.blockers)
    assert "Docker Desktop" in text and "WSL2" in text
    assert "docker build -t gatekeeper-tools:latest -f container/Dockerfile ." in text
    assert "gatekeeper container check" in text
    assert env.engine_path is None


def test_kontener_bez_obrazu_podaje_powod(kontener, monkeypatch) -> None:
    monkeypatch.setattr(container, "_image_present", lambda engine, name: False)
    monkeypatch.setattr(container, "_engine_problem", lambda engine_path: None)
    env = env_mod.collect()
    assert not env.can_run and not env.image_present
    assert env.isolation_reason.startswith("brak obrazu")


def test_nieznany_backend_blokuje(monkeypatch) -> None:
    monkeypatch.setenv(runner.BACKEND_ENV, "nic")
    monkeypatch.setattr(env_mod, "_probe_cache", None)
    env = env_mod.collect()
    assert env.backend == "nieznany" and not env.can_run


def test_strona_srodowiska_w_trybie_kontenera(kontener, panel) -> None:
    strona = panel.get("/srodowisko").text
    assert "kontener (Docker/Podman)" in strona
    assert "/usr/bin/docker" in strona
    assert container.image() in strona
    assert "w obrazie" in strona


def test_domyslny_katalog_stanu_windows(monkeypatch) -> None:
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\jan\AppData\Local")
    assert config.default_state_dir() == Path(r"C:\Users\jan\AppData\Local") / "gatekeeper-web"
    monkeypatch.delenv("LOCALAPPDATA")
    assert config.default_state_dir() == Path.home() / "AppData" / "Local" / "gatekeeper-web"


def test_domyslny_katalog_stanu_posix(monkeypatch) -> None:
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.default_state_dir() == Path.home() / ".local" / "state" / "gatekeeper-web"
    monkeypatch.delenv("GATEKEEPER_WEB_STATE_DIR", raising=False)
    assert Settings.from_env().state_dir == config.default_state_dir()
