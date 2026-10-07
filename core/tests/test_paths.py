"""Normalizacja ścieżek: postacie windowsowe, `file://` i alias `/work` —
testowane na stringach, więc działa też na Linuksie."""

from __future__ import annotations

from pathlib import Path

import pytest

from gatekeeper_core.adapters.base import relative_to_repo
from gatekeeper_core.core.paths import CONTAINER_WORKDIR, is_absolute_path


class _WinRepo:
    """Namiastka repo o ścieżce windowsowej (str() daje `C:\\proj\\repo`)."""

    def __str__(self) -> str:
        return "C:\\proj\\repo"

    def resolve(self):
        return self


@pytest.mark.parametrize(
    "path",
    [
        "file:///C:/proj/repo/src/a.py",
        "file:///c:/proj/repo/src/a.py",
        "C:\\proj\\repo\\src\\a.py",
        "C:/proj/repo/src/a.py",
    ],
)
def test_sciezki_windowsowe_sprowadzane_do_repo(path):
    assert relative_to_repo(path, _WinRepo()) == "src/a.py"  # type: ignore[arg-type]


def test_uri_z_procentami_i_dyskiem():
    assert relative_to_repo("file:///C:/proj/repo/my%20dir/a.py", _WinRepo()) == "my dir/a.py"  # type: ignore[arg-type]


def test_windows_poza_repo_zostaje_bezwzgledna_posix():
    assert relative_to_repo("D:\\other\\a.py", _WinRepo()) == "D:/other/a.py"  # type: ignore[arg-type]


def test_wzgledna_z_backslashem():
    assert relative_to_repo("src\\a.py", Path("/repo")) == "src/a.py"
    assert relative_to_repo(".\\src\\a.py", Path("/repo")) == "src/a.py"


def test_file_uri_posix(tmp_path):
    target = tmp_path / "x.py"
    assert relative_to_repo(f"file://{target}", tmp_path) == "x.py"


def test_alias_kontenera_work():
    assert CONTAINER_WORKDIR == "/work"
    assert relative_to_repo("/work/src/a.py", Path("/home/u/repo")) == "src/a.py"
    assert relative_to_repo("file:///work/src/a.py", Path("/home/u/repo")) == "src/a.py"
    # Spoza /work i spoza repo — bez zmian, bezwzględna.
    assert relative_to_repo("/etc/passwd", Path("/home/u/repo")) == "/etc/passwd"
    # `/workshop` to nie `/work`.
    assert relative_to_repo("/workshop/a.py", Path("/home/u/repo")) == "/workshop/a.py"


def test_is_absolute_path():
    assert is_absolute_path("/a/b")
    assert is_absolute_path("C:/a")
    assert is_absolute_path("C:\\a")
    assert is_absolute_path("\\\\host\\share\\a")
    assert not is_absolute_path("src/a.py")
    assert not is_absolute_path("a:b")
