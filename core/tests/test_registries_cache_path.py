"""Nazwa pakietu z manifestu nie może wyprowadzić pliku cache poza katalog."""

from __future__ import annotations

import pytest

from gatekeeper_core.deps.registries import DiskCache


@pytest.mark.parametrize(
    "name",
    ["..\\..\\x", "a:b", "../x", "/etc/passwd", "C:\\Windows\\x", "..", "@scope/pkg", "a\x00b"],
)
def test_sciezka_cache_zostaje_w_katalogu(tmp_path, name):
    cache = DiskCache(tmp_path)
    path = cache._path("npm", name)

    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.parent.parent == tmp_path
    for forbidden in ("\\", ":", "/", "\x00"):
        assert forbidden not in path.name


def test_rozne_nazwy_daja_rozne_pliki(tmp_path):
    cache = DiskCache(tmp_path)
    assert cache._path("npm", "a/b") != cache._path("npm", "a__b")
    assert cache._path("npm", "x") != cache._path("pypi", "x")
