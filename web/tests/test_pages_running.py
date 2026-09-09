"""Ekrany etapów 2–5: środowisko, projekt, nowa kontrola, zadania, polityki."""

from __future__ import annotations

from conftest import MINIMALNA_POLITYKA, GitRepo, Panel


def test_ekran_srodowiska_mowi_o_izolacji(panel: Panel) -> None:
    strona = panel.get("/srodowisko").text
    assert "Izolacja" in strona
    assert "Bubblewrap" in strona
    # Lista bramek pochodzi z entry pointów.
    assert "G0.scope" in strona


def test_strona_projektu_pokazuje_referencje_i_polityke(
    panel: Panel, gotowy_projekt: int
) -> None:
    strona = panel.get(f"/projekty/{gotowy_projekt}").text
    assert "gotowy do uruchamiania" in strona
    assert "praca" in strona and "main" in strona
    assert "aktywna wersja" in strona


def test_projekt_bez_repozytorium_nie_udaje_gotowego(panel: Panel) -> None:
    project_id = panel.create_project("Sam import")
    strona = panel.get(f"/projekty/{project_id}").text
    assert "tylko przeglądanie raportów" in strona
    assert "Brak zarejestrowanego repozytorium" in strona


def test_zla_sciezka_w_formularzu_wraca_z_komunikatem(panel: Panel) -> None:
    project_id = panel.create_project("Zły katalog")
    response = panel.post(
        f"/projekty/{project_id}/ustawienia",
        data={"name": "Zły katalog", "repo_path": "/etc"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "poza katalogami dozwolonymi" in response.text


def test_formularz_nowej_kontroli_pokazuje_zakres_przed_uruchomieniem(
    panel: Panel, gotowy_projekt: int
) -> None:
    response = panel.post(
        "/nowa-kontrola/podglad",
        data={"project_id": gotowy_projekt, "base": "main", "head": "HEAD", "fast_path": "1"},
    )

    assert response.status_code == 200
    assert "Dokładny zakres tej kontroli" in response.text
    assert "Merge-base użyty w diffie" in response.text
    assert "Uruchom kontrolę" in response.text
    assert "src/app.py" in response.text


def test_uruchomienie_z_formularza_prowadzi_do_zadania(
    panel: Panel, gotowy_projekt: int
) -> None:
    response = panel.post(
        "/nowa-kontrola",
        data={
            "project_id": gotowy_projekt,
            "base": "main",
            "head": "HEAD",
            "fast_path": "1",
            "idempotency_key": "z-formularza",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    strona = panel.get(response.headers["location"]).text
    assert "Zamrożone wejście" in strona
    assert "w kolejce" in strona
    # Zadanie bez raportu nie udaje, że ma decyzję polityki.
    assert "decyzja polityki istnieje tylko dla zadania z zapisanym raportem" in strona


def test_lista_zadan_ostrzega_o_braku_nadzorcy(panel: Panel, gotowy_projekt: int) -> None:
    panel.post_json("/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"})
    strona = panel.get("/zadania").text
    assert "Nadzorca kolejki nie działa" in strona
    assert "brak raportu" in strona


def test_zatrzymanie_z_formularza(panel: Panel, gotowy_projekt: int) -> None:
    job_id = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]["id"]

    response = panel.post(f"/zadania/{job_id}/anuluj", follow_redirects=True)

    assert response.status_code == 200
    assert "anulowane" in response.text


def test_ekran_polityk_i_szkicu(panel: Panel) -> None:
    profile = panel.post_json("/api/v1/policies", {"name": "Zespołowy"}).json()["profile"]

    response = panel.post(
        f"/polityki/{profile['id']}/szkice",
        data={"policy_yaml": MINIMALNA_POLITYKA, "author": "operator", "note": "start"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Polityka przechodzi walidację" in response.text
    assert "Aktywuj tę wersję" in response.text


def test_szkic_z_literowka_nie_da_sie_aktywowac(panel: Panel) -> None:
    profile = panel.post_json("/api/v1/policies", {"name": "Z błędem"}).json()["profile"]
    revision = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts",
        {"policy_yaml": "version: 1\nblocking:\n  - secrets.found_in_dif\n"},
    ).json()["revision"]

    strona = panel.get(f"/polityki/wersje/{revision['id']}").text
    assert "nie przechodzi walidacji" in strona
    assert "Aktywuj tę wersję" not in strona

    odmowa = panel.post(f"/polityki/wersje/{revision['id']}/aktywuj", data={})
    assert odmowa.status_code == 422


def test_porownanie_wskazuje_rozluznienia(panel: Panel) -> None:
    profile = panel.post_json("/api/v1/policies", {"name": "Porównanie"}).json()["profile"]
    surowa = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts",
        {"policy_yaml": "version: 1\nblocking:\n  - secrets.found_in_diff\n"},
    ).json()["revision"]
    panel.post_json(f"/api/v1/policy-revisions/{surowa['id']}/activate", {"author": "op"})
    luzna = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts",
        {"policy_yaml": "version: 1\nblocking: []\nwarn_only:\n  - G3.secrets\n"},
    ).json()["revision"]

    strona = panel.get(f"/polityki/wersje/{luzna['id']}").text

    assert "Co się zmieni wobec wersji v1" in strona
    assert "rozluźnia" in strona
    assert "przestaje blokować" in strona


def test_aktywacja_nie_zmienia_zapisanych_raportow(
    panel: Panel, demo: tuple[int, str], gotowy_projekt: int
) -> None:
    project_id, run_id = demo
    przed = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]

    profile = panel.post_json("/api/v1/policies", {"name": "Nowa"}).json()["profile"]
    revision = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts", {"policy_yaml": MINIMALNA_POLITYKA}
    ).json()["revision"]
    panel.post_json(f"/api/v1/policy-revisions/{revision['id']}/activate", {"author": "op"})

    po = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]
    assert po == przed


def test_pulpit_pokazuje_kolejke(panel: Panel, gotowy_projekt: int) -> None:
    panel.post_json("/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"})
    strona = panel.get("/").text
    assert "Kolejka" in strona
    assert "Zadanie #" in strona


def test_repozytorium_znika_po_rejestracji(panel: Panel, git_repo: GitRepo) -> None:
    """Ścieżka jest sprawdzana także przy uruchomieniu, nie tylko przy zapisie."""
    project_id = panel.create_project("Znikające repo")
    profile = panel.post_json("/api/v1/policies", {"name": "P"}).json()["profile"]
    revision = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts", {"policy_yaml": MINIMALNA_POLITYKA}
    ).json()["revision"]
    panel.post_json(f"/api/v1/policy-revisions/{revision['id']}/activate", {})
    panel.patch_json(
        f"/api/v1/projects/{project_id}",
        {"repo_path": str(git_repo.path), "policy_profile_id": profile["id"]},
    )

    import shutil

    shutil.rmtree(git_repo.path)

    response = panel.post_json("/api/v1/jobs", {"project_id": project_id, "base": "main"})
    assert response.status_code == 409
    assert "nie istnieje" in response.json()["detail"]
