"""Przenośność (Windows) i poprawki bezpieczeństwa etapu W0, sprawdzalne na Linuksie."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from gatekeeper_core.core import change as change_module
from gatekeeper_core.core import fsutil, runner
from gatekeeper_core.core.change import ChangeContext, GitError, _git, write_worktree_file
from gatekeeper_core.core.fsutil import is_link, remove_tree
from gatekeeper_core.core.runner import Sandbox, SandboxUnavailable, dependency_paths
from tests.conftest import symlink_or_skip


def _change(repo) -> ChangeContext:
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "linia1\nlinia2\n")
    repo.commit("zmiana")
    return ChangeContext.from_git(repo.path, "main")


@pytest.fixture
def global_git_config(tmp_path, monkeypatch):
    """Globalny config Git sterowany testem — jak u użytkownika na Windows."""
    config = tmp_path / "global.gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


# ---------- kopia robocza bramki (change.worktree_at) ----------


def test_kopia_nie_uzywa_dev_null_jako_katalogu_hookow(repo, monkeypatch):
    change = _change(repo)
    calls: list[tuple[str, ...]] = []
    original = change_module._git

    def recording(path, *args):
        calls.append(args)
        return original(path, *args)

    monkeypatch.setattr(change_module, "_git", recording)
    with change.worktree_at(change.head_sha) as target:
        hooks_values = {
            arg.split("=", 1)[1]
            for args in calls
            for arg in args
            if arg.startswith("core.hooksPath=")
        }
        commands = [args for args in calls if args[0] == "-c"]
        assert [c[6] for c in commands] == ["init", "fetch", "checkout"]
        assert all(f"core.hooksPath={next(iter(hooks_values))}" in c for c in commands)
        assert len(hooks_values) == 1
        hooks = Path(next(iter(hooks_values)))
        assert hooks != Path("/dev/null")
        # Pusty katalog w prywatnym mkdtemp, ale poza kopią kodu PR-a.
        assert hooks.is_dir() and not any(hooks.iterdir())
        assert hooks.parent == target.parent
        assert not hooks.is_relative_to(target)
    assert not hooks.exists()


def test_hook_z_globalnego_configu_nie_wykonuje_sie_przy_kopii(
    repo, tmp_path, global_git_config
):
    """Odpowiednik podłożonego `C:\\dev\\null\\post-checkout`: obce hooki nie ruszają."""
    change = _change(repo)
    attacker = tmp_path / "attacker-hooks"
    attacker.mkdir()
    marker = tmp_path / "hook-wykonany"
    for name in ("post-checkout", "reference-transaction"):
        hook = attacker / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
    global_git_config.write_text(f"[core]\n\thooksPath = {attacker.as_posix()}\n", encoding="utf-8")

    with change.worktree_at(change.head_sha) as target:
        assert (target / "src/app.py").is_file()
    assert not marker.exists()


def test_kopia_ma_bajty_commita_mimo_globalnego_autocrlf(repo, global_git_config):
    change = _change(repo)
    global_git_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")

    with change.worktree_at(change.head_sha) as target:
        content = (target / "src/app.py").read_bytes()
    assert content == b"linia1\nlinia2\n"


# ---------- sprzątanie (fsutil.remove_tree) ----------


def _readonly_tree(root: Path) -> None:
    (root / "objects" / "ab").mkdir(parents=True)
    obj = root / "objects" / "ab" / "cdef"
    obj.write_bytes(b"blob")
    obj.chmod(stat.S_IREAD)
    (root / "objects" / "ab").chmod(stat.S_IREAD | stat.S_IEXEC)
    (root / "plik.txt").write_text("x", encoding="utf-8")
    (root / "plik.txt").chmod(stat.S_IREAD)


def test_remove_tree_usuwa_drzewo_z_plikami_tylko_do_odczytu(tmp_path):
    root = tmp_path / "kopia"
    _readonly_tree(root)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert remove_tree(root) is True
    assert not root.exists()


def test_remove_tree_nie_podaza_za_dowiazaniem(tmp_path):
    outside = tmp_path / "poza"
    outside.mkdir()
    (outside / "cenny.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    symlink_or_skip(link, outside, True)
    assert remove_tree(link) is True
    assert not link.exists() and not link.is_symlink()
    assert (outside / "cenny.txt").is_file()


def test_remove_tree_ostrzega_zamiast_milczec(tmp_path, monkeypatch):
    root = tmp_path / "kopia"
    root.mkdir()

    def broken(*_args, **_kwargs):
        raise PermissionError("zablokowany przez inny proces")

    monkeypatch.setattr(fsutil.shutil, "rmtree", broken)
    with pytest.warns(RuntimeWarning, match="nie udało się usunąć"):
        assert remove_tree(root) is False


def test_remove_tree_brak_katalogu_to_sukces(tmp_path):
    assert remove_tree(tmp_path / "nie-ma") is True


# ---------- dowiązania i junction (fsutil.is_link) ----------


def test_is_link_rozpoznaje_symlink(tmp_path):
    target = tmp_path / "cel"
    target.mkdir()
    (tmp_path / "plik").write_text("x", encoding="utf-8")
    symlink_or_skip(tmp_path / "link", target, True)
    assert is_link(tmp_path / "link")
    assert not is_link(target)
    assert not is_link(tmp_path / "plik")
    assert not is_link(tmp_path / "nie-ma")


@pytest.mark.skipif(sys.platform != "win32", reason="junction istnieje tylko na NTFS")
def test_is_link_rozpoznaje_junction(tmp_path):  # pragma: no cover - Windows
    import _winapi  # type: ignore[import-not-found]

    target = tmp_path / "cel"
    target.mkdir()
    junction = tmp_path / "junction"
    _winapi.CreateJunction(str(target), str(junction))
    assert not junction.is_symlink()
    assert is_link(junction)


def _pretend_junction(monkeypatch, junction: Path) -> None:
    original = Path.is_junction

    def is_junction(self: Path) -> bool:
        return self == junction or original(self)

    monkeypatch.setattr(Path, "is_junction", is_junction)


def test_nakladanie_testow_odrzuca_junction(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    _pretend_junction(monkeypatch, work / "tests")
    with pytest.raises(GitError, match="dowiązanie"):
        write_worktree_file(work, "tests/test.py", "replacement")
    assert not (work / "tests" / "test.py").exists()


def test_node_modules_jako_junction_jest_niezaufane(tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    _pretend_junction(monkeypatch, tmp_path / "node_modules")
    with pytest.raises(SandboxUnavailable, match="dowiązaniem"):
        dependency_paths(tmp_path)


# ---------- import i Sandbox poza Linuksem ----------


def test_runner_nie_importuje_resource_bezwarunkowo():
    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    top_level = [
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "resource" not in top_level


def test_sandbox_bez_izolacji_odmawia_przed_budowa_limitow(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEKEEPER_SANDBOX", "bwrap")
    monkeypatch.setattr(runner, "filesystem_isolation_available", lambda: False)

    def forbidden(self):
        raise AssertionError("preexec_fn nie może powstać przed kontrolą izolacji")

    monkeypatch.setattr(Sandbox, "_limits", forbidden)
    with pytest.raises(SandboxUnavailable, match="Bubblewrap"):
        Sandbox().run([sys.executable, "-c", "pass"], cwd=tmp_path)


def test_gatekeeper_core_importuje_sie_bez_modulow_posix():
    """Symulacja Windows: brak `resource`/`fcntl`/... i `sys.platform == "win32"`.

    Zależności zewnętrzne ładujemy przed podmianą platformy — sprawdzamy
    wyłącznie moduły `gatekeeper_core`.
    """
    script = textwrap.dedent(
        """
        import importlib, pkgutil, sys
        import gatekeeper_core

        modules = pkgutil.walk_packages(gatekeeper_core.__path__, "gatekeeper_core.")
        names = [m.name for m in modules]
        for name in names:
            importlib.import_module(name)
        for name in [k for k in sys.modules if k.startswith("gatekeeper_core")]:
            del sys.modules[name]
        for name in ("resource", "fcntl", "termios", "pwd", "grp"):
            sys.modules[name] = None
        sys.platform = "win32"
        failed = []
        for name in names:
            try:
                importlib.import_module(name)
            except Exception as exc:
                failed.append(f"{name}: {exc!r}")
        from gatekeeper_core.core import runner
        assert runner.filesystem_isolation_available() is False
        print("\\n".join(failed))
        sys.exit(1 if failed else 0)
        """
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_konfiguracja_kopii_jest_trwala_w_jej_git_config(repo, tmp_path, monkeypatch):
    # Bramki wołają git w kopii bez `-c` — globalny autocrlf/hooksPath nie może
    # tam wrócić.
    hostile = tmp_path / "global.gitconfig"
    hostile.write_text("[core]\n\tautocrlf = true\n\thooksPath = /tmp/zle-hooki\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    repo.write("a.txt", "x\n")
    repo.commit("baza")
    change = ChangeContext.from_git(repo.path, "HEAD", "HEAD")

    with change.worktree_at(change.head_sha) as copy:
        hooks = _git(copy, "config", "core.hooksPath").strip()
        autocrlf = _git(copy, "config", "core.autocrlf").strip()

    assert hooks != "/tmp/zle-hooki" and hooks.endswith("/hooks")
    assert autocrlf == "false"
