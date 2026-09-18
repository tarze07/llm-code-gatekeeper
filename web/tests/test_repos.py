"""Rejestracja repozytorium i wybór zakresu Git — granica zaufania etapu 2."""

from __future__ import annotations

import os
import subprocess
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


# ------------------------------------------------- commity w podglądzie zakresu


def test_podglad_wypisuje_commity_zakresu(git_repo: GitRepo) -> None:
    git_repo.write("src/app.py", "def hello():\n    return 'jeszcze raz'\n")
    git_repo.commit("druga zmiana")

    preview = preview_scope(git_repo.path, "main", "HEAD")

    assert preview.total_commits == 2
    assert not preview.commits_truncated
    # Od najnowszego — operator czyta listę tak, jak czyta `git log`.
    assert [c.subject for c in preview.commits] == ["druga zmiana", "zmiana"]
    assert all(len(c.sha) == 40 for c in preview.commits)
    assert preview.commits[0].short_sha == preview.commits[0].sha[:12]
    assert preview.commits[0].author == "Panel"


def test_commity_licza_sie_od_merge_base_a_nie_od_czubka_bazy(git_repo: GitRepo) -> None:
    """Lista ma pokazywać to samo, co oceniany diff — inaczej myli tam,
    gdzie operator decyduje o uruchomieniu."""
    git_repo.checkout("main")
    git_repo.write("inne.md", "# obcy commit\n")
    git_repo.commit("commit na main po odgałęzieniu")
    git_repo.checkout("praca")

    preview = preview_scope(git_repo.path, "main", "HEAD")

    assert preview.uses_merge_base
    tematy = [c.subject for c in preview.commits]
    assert "zmiana" in tematy
    assert "commit na main po odgałęzieniu" not in tematy


def test_pusty_zakres_nie_ma_commitow(git_repo: GitRepo) -> None:
    preview = preview_scope(git_repo.path, "praca", "praca")

    assert preview.total_commits == 0
    assert preview.commits == ()
    assert not preview.commits_truncated


def test_dluga_historia_jest_ucinana_z_podaniem_pelnej_liczby(git_repo: GitRepo) -> None:
    """Gałąź odbiegająca o tysiąc commitów nie ma renderować tysiąca wierszy."""
    from dataclasses import replace

    from gatekeeper_web.services.repos import list_commits

    for i in range(5):
        git_repo.write(f"plik-{i}.txt", f"{i}\n")
        git_repo.commit(f"commit {i}")

    preview = preview_scope(git_repo.path, "main", "HEAD")
    commits, total = list_commits(
        git_repo.path, preview.merge_base, preview.head_sha, limit=3
    )

    # Ucięta lista, ale pełna liczba — inaczej operator zobaczyłby zaniżony zakres.
    assert total == preview.total_commits == 6
    assert len(commits) == 3
    assert replace(preview, commits=commits).commits_truncated
    assert not preview.commits_truncated


# ------------------------------------------- podpowiadana gałąź bazowa


def _repo(tmp_path: Path, branch: str) -> GitRepo:
    repo = GitRepo(tmp_path / f"repo-{branch.replace('/', '-')}")
    repo.git("checkout", "-q", "-b", branch)
    repo.write("README.md", "# projekt\n")
    repo.commit("start")
    return repo


def test_podpowiada_master_gdy_repo_nie_ma_main(tmp_path: Path) -> None:
    """Sztywne `main` w formularzu dawało błąd na każdym repo z `master`."""
    from gatekeeper_web.services.repos import default_base_ref

    assert default_base_ref(_repo(tmp_path, "master").path) == "master"


def test_podpowiada_main_gdy_jest(tmp_path: Path) -> None:
    from gatekeeper_web.services.repos import default_base_ref

    assert default_base_ref(_repo(tmp_path, "main").path) == "main"


def test_bez_typowej_galezi_nie_zgaduje(tmp_path: Path) -> None:
    """Puste pole jest uczciwsze niż nazwa, o której wiadomo, że bywa popularna."""
    from gatekeeper_web.services.repos import default_base_ref

    assert default_base_ref(_repo(tmp_path, "produkcja").path) == ""


def test_galaz_domyslna_zdalnego_wygrywa_z_lista_typowych(tmp_path: Path) -> None:
    """Repozytorium deklarujące `origin/HEAD` samo mówi, co jest bazą."""
    from gatekeeper_web.services.repos import default_base_ref

    zrodlo = _repo(tmp_path, "produkcja")
    klon = tmp_path / "klon"
    subprocess.run(
        ["git", "clone", "-q", str(zrodlo.path), str(klon)], check=True, capture_output=True
    )
    # Klon ma też lokalne `main`, ale `origin/HEAD` wskazuje `produkcja`.
    subprocess.run(
        ["git", "-C", str(klon), "branch", "-q", "main"], check=True, capture_output=True
    )

    assert default_base_ref(klon) == "produkcja"


def test_nieznana_wersja_podpowiada_dostepne(git_repo: GitRepo) -> None:
    """Sam komunikat „nie znam tej wersji" zostawiał operatora ze zgadywanką."""
    with pytest.raises(RepoError) as wyjatek:
        resolve_commit(git_repo.path, "main-ktorego-nie-ma")

    komunikat = str(wyjatek.value)
    assert "nie znam wersji" in komunikat
    assert "dostępne m.in.:" in komunikat
    assert "praca" in komunikat


def test_zapis_wzgledny_wskazuje_rodzica(git_repo: GitRepo) -> None:
    """`60d1046^` było odrzucane, więc SHA rodzica trzeba było znaleźć poza panelem."""
    czubek = git_repo.git("rev-parse", "HEAD").strip()
    rodzic = git_repo.git("rev-parse", "HEAD~1").strip()

    assert resolve_commit(git_repo.path, f"{czubek[:12]}^") == rodzic
    assert resolve_commit(git_repo.path, "HEAD~1") == rodzic
    assert resolve_commit(git_repo.path, "praca^") == rodzic


def test_zapis_wzgledny_nie_otwiera_wstrzykniecia_opcji() -> None:
    """Rozluźnienie dotyczy `^` i `~`, nie pierwszego znaku."""
    for zly in ("--upload-pack=evil", "-x", "^HEAD", "~1"):
        with pytest.raises(RepoError):
            validate_ref_name(zly)


def test_ostatnie_commity_obejmuja_wszystkie_galezie(git_repo: GitRepo) -> None:
    """Commit, od którego liczy się diff, zwykle nie jest czubkiem niczego."""
    from gatekeeper_web.services.repos import recent_commits

    git_repo.checkout("main")
    git_repo.write("na-main.md", "# tylko na main\n")
    git_repo.commit("commit wyłącznie na main")
    git_repo.checkout("praca")

    tematy = [c.subject for c in recent_commits(git_repo.path)]

    assert "commit wyłącznie na main" in tematy
    assert "zmiana" in tematy
    assert all(len(c.sha) == 40 for c in recent_commits(git_repo.path))
