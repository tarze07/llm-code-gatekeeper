"""Homoglify: nazwa, która dla oka jest popularnym pakietem, ma się z nim zwinąć."""

from __future__ import annotations

import pytest

from gatekeeper_core.deps.manifests import NPM, PYPI
from gatekeeper_core.deps.typosquat import canonical, nearest_popular


@pytest.mark.parametrize(
    ("attack", "target"),
    [
        ("reqυests", "requests"),  # grecka υ
        ("requеsts", "requests"),  # cyrylickie е
        ("ɡit", "git"),  # łacińskie IPA ɡ
        ("ｒｅｑｕｅｓｔｓ", "requests"),  # pełna szerokość → NFKC
        ("djanɡo", "django"),
        ("ρandas", "pandas"),  # grecka ρ
        ("nυmpy", "numpy"),
        ("ӏodash", "lodash"),  # cyrylickie ӏ
        ("ехрress", "express"),  # cyrylickie е, х, р
        ("ԁjango", "django"),  # cyrylickie ԁ
        ("ѕcıpy", "scipy"),  # cyrylickie ѕ, łacińskie ı
    ],
)
def test_homoglif_zwija_sie_do_lacinskiego_odpowiednika(attack, target):
    assert canonical(PYPI, attack) == canonical(PYPI, target)


@pytest.mark.parametrize(
    ("ecosystem", "attack", "target"),
    [(NPM, "ехрress", "express"), (NPM, "ӏodash", "lodash"), (PYPI, "ρandas", "pandas")],
)
def test_homoglif_jest_sasiadem_popularnego_pakietu_z_dystansem_zero(ecosystem, attack, target):
    hits = nearest_popular(ecosystem, attack)

    assert hits and hits[0].candidate == target
    assert hits[0].distance == 0


def test_zwykla_nazwa_ascii_sie_nie_zmienia():
    assert canonical(PYPI, "httpx") == "httpx"
