"""Rejestr bramek: ta sama klasa dwa razy to nie błąd, inna pod zajętym `id` — tak."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gatekeeper_core import gates
from gatekeeper_core.gates import Gate, all_gates, register


class _Fake(Gate):
    id = "GX.fake"


class _Impostor(Gate):
    id = "GX.fake"


@pytest.fixture
def registry(monkeypatch):
    fresh: dict[str, type[Gate]] = {}
    monkeypatch.setattr(gates, "REGISTRY", fresh)
    return fresh


def _entry_points(*classes):
    return lambda group: [SimpleNamespace(load=lambda c=c: c) for c in classes]


def test_ta_sama_klasa_z_dekoratora_i_entry_pointu_przechodzi(registry, monkeypatch):
    register(_Fake)
    monkeypatch.setattr(gates, "entry_points", _entry_points(_Fake))

    assert all_gates() == [_Fake]


def test_inna_klasa_pod_zajetym_id_jest_bledem(registry):
    register(_Fake)

    with pytest.raises(ValueError, match="GX.fake"):
        register(_Impostor)
    assert registry["GX.fake"] is _Fake


def test_entry_point_z_kolizja_id_jest_bledem_a_nie_cicho_pomijany(registry, monkeypatch):
    register(_Fake)
    monkeypatch.setattr(gates, "entry_points", _entry_points(_Impostor))

    with pytest.raises(ValueError, match="nie może jej zastąpić"):
        all_gates()


def test_prawdziwe_bramki_rejestruja_sie_bez_kolizji():
    ids = [cls.id for cls in all_gates()]

    assert len(ids) == len(set(ids))
    assert "G1.static" in ids
