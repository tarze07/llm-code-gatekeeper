"""Profile polityki: szkic → walidacja → porównanie → jawna aktywacja."""

from __future__ import annotations

from pathlib import Path

import pytest

from gatekeeper_web.services import policies as service
from gatekeeper_web.storage import Database, PolicyStore, PolicyStoreError

DOBRA = """
version: 1
blocking:
  - secrets.found_in_diff
thresholds:
  coverage.diff_ratio:
    min: 0.80
warn_only:
  - G3.sast
"""

LUZNIEJSZA = """
version: 1
blocking: []
thresholds:
  coverage.diff_ratio:
    min: 0.50
warn_only:
  - G3.sast
  - G3.secrets
"""

Z_LITEROWKA = """
version: 1
blocking:
  - secrets.found_in_dif
"""


@pytest.fixture
def store(tmp_path: Path) -> PolicyStore:
    return PolicyStore(Database(tmp_path / "panel.db"))


def test_wersja_powstaje_jako_szkic(store: PolicyStore) -> None:
    profile = store.create_profile("Domyślny")
    revision = store.create_revision(profile.id, DOBRA)

    assert revision.state == "draft"
    # Sam zapis szkicu niczego nie aktywuje.
    assert store.active_revision(profile.id) is None


def test_aktywacja_jest_jawna_i_wycofuje_poprzednia(store: PolicyStore) -> None:
    profile = store.create_profile("Domyślny")
    first = store.create_revision(profile.id, DOBRA)
    store.activate(first.id, author="operator", note="start")
    second = store.create_revision(profile.id, LUZNIEJSZA)
    store.activate(second.id, author="operator", note="luzniej")

    active = store.active_revision(profile.id)
    assert active is not None and active.id == second.id
    retired = store.get_revision(first.id)
    assert retired is not None and retired.state == "retired"


def test_wycofanej_wersji_nie_da_sie_wskrzesic(store: PolicyStore) -> None:
    profile = store.create_profile("Domyślny")
    first = store.create_revision(profile.id, DOBRA)
    store.activate(first.id)
    store.activate(store.create_revision(profile.id, LUZNIEJSZA).id)

    with pytest.raises(PolicyStoreError, match="wycofana"):
        store.activate(first.id)


def test_walidacja_lapie_literowke_w_fakcie(store: PolicyStore, tmp_path: Path) -> None:
    profile = store.create_profile("Domyślny")
    revision = store.create_revision(profile.id, Z_LITEROWKA)

    result = service.validate(revision, tmp_path / "snapshot")

    assert not result.ok
    assert any("secrets.found_in_dif" in err for err in result.errors)


def test_walidacja_dobrej_polityki_daje_podsumowanie(store: PolicyStore, tmp_path: Path) -> None:
    profile = store.create_profile("Domyślny")
    result = service.validate(store.create_revision(profile.id, DOBRA), tmp_path / "s")

    assert result.ok
    assert result.summary["blocking"] == ["secrets.found_in_diff"]
    assert result.summary["warn_only"] == ["G3.sast"]
    assert result.summary["thresholds"]["coverage.diff_ratio"]["min"] == 0.80


def test_porownanie_wskazuje_ktore_blokady_staja_sie_ostrzezeniami(
    store: PolicyStore, tmp_path: Path
) -> None:
    profile = store.create_profile("Domyślny")
    stara = service.validate(store.create_revision(profile.id, DOBRA), tmp_path / "a")
    nowa = service.validate(store.create_revision(profile.id, LUZNIEJSZA), tmp_path / "b")

    changes = service.compare(stara.summary, nowa.summary)
    rozluznienia = [c for c in changes if c.loosens]

    assert any("secrets.found_in_diff" in c.detail for c in rozluznienia)
    assert any("G3.secrets" in c.detail and "przestaje blokować" in c.detail for c in rozluznienia)
    assert any("coverage.diff_ratio" in c.detail for c in rozluznienia)


def test_snapshot_zapisuje_wszystkie_trzy_pliki(store: PolicyStore, tmp_path: Path) -> None:
    profile = store.create_profile("Domyślny")
    revision = store.create_revision(
        profile.id,
        DOBRA,
        exceptions_yaml="exemptions: []\n",
        scope_map_yaml="components:\n  AUTH:\n    - 'src/**'\n",
    )

    snapshot = service.materialize(revision, tmp_path / "snapshot")

    assert snapshot.policy_path.read_text(encoding="utf-8") == DOBRA
    assert snapshot.exceptions_path is not None
    assert snapshot.scope_map_path is not None
    # Nazwy plików takie jak w repozytorium — `Policy.load()` szuka wyjątków obok.
    assert snapshot.exceptions_path.name == "exceptions.yaml"


def test_wygasly_wyjatek_jest_bledem_walidacji(store: PolicyStore, tmp_path: Path) -> None:
    profile = store.create_profile("Domyślny")
    revision = store.create_revision(
        profile.id,
        DOBRA,
        exceptions_yaml=(
            "exceptions:\n"
            "  - rule: secrets.found_in_diff\n"
            "    owner: zespol\n"
            "    reason: stary sekret\n"
            "    expires: 2020-01-01\n"
        ),
    )

    result = service.validate(revision, tmp_path / "s")

    assert not result.ok
    assert result.expired


def test_polityka_startowa_przechodzi_walidacje(tmp_path: Path) -> None:
    """Profil startowy aktywuje się jednym kliknięciem — więc musi być poprawny.

    Ten test jest po to, żeby zmiana w core (nowa nazwa faktu, usunięta bramka)
    wywaliła się tutaj, a nie u operatora zakładającego pierwszy projekt.
    """
    store = PolicyStore(Database(tmp_path / "panel.db"))
    profile = store.create_profile("Startowa")
    startowa = service.starter_policy()
    revision = store.create_revision(
        profile.id,
        startowa.policy_yaml,
        startowa.exceptions_yaml,
        startowa.scope_map_yaml,
    )

    wynik = service.validate(revision, tmp_path / "snapshot")
    assert wynik.ok, wynik.errors
    # Wyjątki w pliku startowym są zakomentowane: polityka nie może wygasnąć
    # z upływem czasu i zablokować aktywacji komuś, kto instaluje panel za rok.
    assert wynik.expired == []


def test_polityka_startowa_faktycznie_bramkuje(tmp_path: Path) -> None:
    """Pusty `version: 1` też przechodził walidację — i nie blokował niczego."""
    store = PolicyStore(Database(tmp_path / "panel.db"))
    profile = store.create_profile("Startowa")
    startowa = service.starter_policy()
    revision = store.create_revision(profile.id, startowa.policy_yaml, None, None)

    podsumowanie = service.validate(revision, tmp_path / "snapshot").summary
    assert "secrets.found_in_diff" in podsumowanie["blocking"]
    assert podsumowanie["thresholds"]
