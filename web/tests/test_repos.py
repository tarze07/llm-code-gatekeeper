"""Rejestracja repozytorium i wybór zakresu Git — granica zaufania etapu 2."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import GitRepo

from gatekeeper_web.services.repos import (
    RepoError,
    list_refs,
    preview_scope,
    resolve_commit,
    resolve_repo_path,
    validate_ref_name,
)


def test_rejestruje_korzen_repozytorium(git_repo: GitRepo, tmp_path: Path) -> None:
    assert resolve_repo_path(str(git_repo.path), (tmp_path,)) == git_repo.path.resolve()


def test_odrzuca_sciezke_poza_dozwolonym_katalogiem(git_repo: GitRepo, tmp_path: Path) -> None:
    with pytest.raises(RepoError, match="poza katalogami dozwolonymi"):
        resolve_repo_path(str(git_repo.path), (tmp_path / "gdzie-indziej",))


def test_dowiazanie_nie_omija_dozwolonego_katalogu(git_repo: GitRepo, tmp_path: Path) -> None:
    """Sprawdzamy ścieżkę **rozwiniętą**, więc symlink nie jest furtką."""
    dozwolony = tmp_path / "dozwolony"
    dozwolony.mkdir()
    skrot = dozwolony / "skrot"
    os.symlink(git_repo.path, skrot)

    with pytest.raises(RepoError, match="poza katalogami dozwolonymi"):
        resolve_repo_path(str(skrot), (dozwolony,))


def test_odrzuca_katalog_bez_gita(tmp_path: Path) -> None:
    zwykly = tmp_path / "zwykly"
    zwykly.mkdir()
    with pytest.raises(RepoError, match="nie jest repozytorium Git"):
        resolve_repo_path(str(zwykly), (tmp_path,))


def test_odrzuca_podkatalog_repozytorium(git_repo: GitRepo, tmp_path: Path) -> None:
    with pytest.raises(RepoError, match="podkatalogiem repozytorium"):
        resolve_repo_path(str(git_repo.path / "src"), (tmp_path,))


def test_nieistniejaca_sciezka_ma_czytelny_blad(tmp_path: Path) -> None:
    with pytest.raises(RepoError, match="nie istnieje"):
        resolve_repo_path(str(tmp_path / "nie-ma"), (tmp_path,))


@pytest.mark.parametrize(
    "ref",
    [
        "--upload-pack=touch /tmp/x",
        "main; rm -rf /",
        "main`whoami`",
        "$(id)",
        "main\nrm",
        "-C/etc",
        "",
        "x" * 300,
    ],
)
def test_pole_tekstowe_nie_staje_sie_opcja_gita(ref: str) -> None:
    with pytest.raises(RepoError, match="niedozwolona nazwa wersji Git"):
        validate_ref_name(ref)


def test_nieznana_wersja_jest_odrzucona(git_repo: GitRepo) -> None:
    with pytest.raises(RepoError, match="nie znam wersji"):
        resolve_commit(git_repo.path, "nie-ma-takiej-galezi")


def test_lista_referencji_zawiera_galezie(git_repo: GitRepo) -> None:
    names = {ref.name for ref in list_refs(git_repo.path)}
    assert {"main", "praca"} <= names


def test_podglad_liczy_zakres_od_merge_base(git_repo: GitRepo) -> None:
    # Gałąź bazowa idzie do przodu po odgałęzieniu — diff nie ma prawa
    # wciągnąć cudzych commitów.
    git_repo.checkout("main")
    git_repo.write("inne.md", "# obcy commit\n")
    git_repo.commit("commit na main po odgałęzieniu")
    git_repo.checkout("praca")

    preview = preview_scope(git_repo.path, "main", "HEAD")

    assert preview.uses_merge_base
    assert preview.merge_base != preview.base_tip_sha
    assert "inne.md" not in preview.paths
    assert "src/app.py" in preview.paths


def test_podglad_odroznia_pliki_generowane(git_repo: GitRepo) -> None:
    git_repo.write("package-lock.json", '{"lockfileVersion": 3}\n')
    git_repo.commit("lockfile")

    preview = preview_scope(git_repo.path, "main", "HEAD")

    assert preview.generated_files == 1
    assert preview.effective_files == preview.total_files - 1
