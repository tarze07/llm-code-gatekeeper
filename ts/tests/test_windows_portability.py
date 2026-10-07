"""Regresje przenośności na Windows (etap W0), uruchamialne na Linuksie:
separator `NODE_PATH`, junctions w `node_modules`, rozwiązywanie `npm`."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from gatekeeper_ts import node
from gatekeeper_ts.testing import toolchain
from gatekeeper_ts.testing.toolchain import IsolationBroken, TsTestToolchain


def test_node_path_entries_uzywa_os_pathsep(monkeypatch):
    monkeypatch.setattr(os, "pathsep", ";")
    env = {"NODE_PATH": r"C:\a\node_modules;D:\b"}
    assert node.node_path_entries(env) == (r"C:\a\node_modules", r"D:\b")
    assert node.node_path_entries({}) == ()


def test_global_node_modules_uzywa_which_i_kodowania(monkeypatch):
    node.global_node_modules.cache_clear()
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return SimpleNamespace(stdout="C:\\npm\\node_modules\n")

    monkeypatch.setattr(node.shutil, "which", lambda name: "C:\\npm\\npm.cmd")
    monkeypatch.setattr(node.subprocess, "run", fake_run)
    try:
        assert node.global_node_modules() == "C:\\npm\\node_modules"
    finally:
        node.global_node_modules.cache_clear()
    assert seen["cmd"] == ["C:\\npm\\npm.cmd", "root", "-g"]
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["errors"] == "replace"


def test_global_node_modules_bez_npm_zwraca_none(monkeypatch):
    node.global_node_modules.cache_clear()
    monkeypatch.setattr(node.shutil, "which", lambda name: None)

    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("nie powinno uruchamiac podprocesu")

    monkeypatch.setattr(node.subprocess, "run", boom)
    try:
        assert node.global_node_modules() is None
    finally:
        node.global_node_modules.cache_clear()


def test_global_node_modules_blad_npm_zwraca_none(monkeypatch):
    node.global_node_modules.cache_clear()
    monkeypatch.setattr(node.shutil, "which", lambda name: "/usr/bin/npm")

    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, "npm")

    monkeypatch.setattr(node.subprocess, "run", fail)
    try:
        assert node.global_node_modules() is None
    finally:
        node.global_node_modules.cache_clear()


def _repo_z_workspace(tmp_path: Path) -> tuple[SimpleNamespace, Path]:
    repo = tmp_path / "repo"
    (repo / "packages" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "@app").mkdir(parents=True)
    return SimpleNamespace(repo=repo), repo / "node_modules" / "@app" / "lib"


def test_izolacja_odrzuca_symlink_do_repo(tmp_path):
    change, entry = _repo_z_workspace(tmp_path)
    entry.symlink_to(change.repo / "packages" / "lib", target_is_directory=True)
    with pytest.raises(IsolationBroken):
        TsTestToolchain()._assert_isolation(change, {})  # type: ignore[arg-type]


def test_izolacja_sprawdza_tez_junction(tmp_path, monkeypatch):
    """Na Windows `is_symlink()` jest False dla junction — kontrola musi
    pytać też `is_junction()`. Na Linuksie udajemy to: wpis jest symlinkiem
    (żeby `resolve()` wyprowadziło do repo), ale `is_symlink` zwraca False."""
    change, entry = _repo_z_workspace(tmp_path)
    entry.symlink_to(change.repo / "packages" / "lib", target_is_directory=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    calls: list[Path] = []

    def fake_is_junction(self: Path) -> bool:
        calls.append(self)
        return self == entry

    monkeypatch.setattr(Path, "is_junction", fake_is_junction)
    with pytest.raises(IsolationBroken):
        TsTestToolchain()._assert_isolation(change, {})  # type: ignore[arg-type]
    assert entry in calls


def test_izolacja_zwykly_katalog_przechodzi(tmp_path):
    change, entry = _repo_z_workspace(tmp_path)
    entry.mkdir()
    TsTestToolchain()._assert_isolation(change, {})  # type: ignore[arg-type]


def test_link_node_modules_na_windows_tworzy_junction(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "node_modules").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    created: list[tuple[str, str]] = []
    fake = types.SimpleNamespace(CreateJunction=lambda s, t: created.append((s, t)))
    monkeypatch.setitem(sys.modules, "_winapi", fake)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    change = SimpleNamespace(repo=repo)
    assert TsTestToolchain()._link_node_modules(change, worktree) is True  # type: ignore[arg-type]
    assert created == [(str(repo / "node_modules"), str(worktree / "node_modules"))]


def test_link_node_modules_na_posix_tworzy_symlink(tmp_path):
    repo = tmp_path / "repo"
    (repo / "node_modules").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    change = SimpleNamespace(repo=repo)
    assert TsTestToolchain()._link_node_modules(change, worktree) is True  # type: ignore[arg-type]
    assert (worktree / "node_modules").is_symlink()
