"""Strony HTML: czy operator faktycznie widzi to, co jest w raporcie."""

from __future__ import annotations

from conftest import GitRepo, Panel


def test_pulpit_dziala_bez_zadnych_danych(panel: Panel) -> None:
    strona = panel.get("/")
    assert strona.status_code == 200
    assert "Zacznij od projektu" in strona.text


def test_pulpit_liczy_decyzje(panel: Panel, demo: tuple[int, str]) -> None:
    strona = panel.get("/").text
    assert "BLOCK — zmiana zatrzymana" in strona


def test_szczegoly_pokazuja_wszystkie_22_znaleziska(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    strona = panel.get(f"/projekty/{project_id}/przebiegi/{run_id}").text

    assert strona.count('<article class="znalezisko"') == 22
    assert "22 z 22" in strona


def test_szczegoly_rozdzielaja_zadanie_bramki_i_polityke(
    panel: Panel, demo: tuple[int, str]
) -> None:
    project_id, run_id = demo
    strona = panel.get(f"/projekty/{project_id}/przebiegi/{run_id}").text

    assert "Zadanie" in strona
    assert "Bramki" in strona
    assert "Polityka" in strona
    assert "zaimportowane" in strona


def test_szczegoly_tlumacza_bramke_pass_lamiaca_polityke(
    panel: Panel, demo: tuple[int, str]
) -> None:
    project_id, run_id = demo
    strona = panel.get(f"/projekty/{project_id}/przebiegi/{run_id}").text

    assert "Co wymaga wyjaśnienia" in strona
    assert "2,7%" in strona
    assert "1 z 37" in strona
    assert "polityka decyduje" in strona


def test_szczegoly_zachowuja_liste_ograniczen(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    strona = panel.get(f"/projekty/{project_id}/przebiegi/{run_id}").text

    assert "Czego ta brama nie sprawdza" in strona
    assert "review semantyczny LLM" in strona


def test_strona_znaleziska_pokazuje_dowod_i_fingerprint(
    panel: Panel, demo: tuple[int, str]
) -> None:
    project_id, run_id = demo
    body = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()
    secrets = next(g for g in body["report"]["gates"] if g["gate"] == "G3.secrets")
    fingerprint = secrets["findings"][0]["fingerprint"]

    strona = panel.get(
        f"/projekty/{project_id}/przebiegi/{run_id}/znaleziska/{fingerprint}"
    ).text
    assert "Scenariusz awarii" in strona
    assert fingerprint in strona
    assert "/tmp/gatekeeper-wt-" not in strona


def test_historia_filtruje_z_formularza(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    panel.import_sample("bez-znalezisk.json", project_id)

    wszystkie = panel.get("/przebiegi").text
    assert "35e102d0d126" in wszystkie and "0000demo0003" in wszystkie

    tylko_block = panel.get("/przebiegi?decyzja=BLOCK").text
    assert "35e102d0d126" in tylko_block
    assert "0000demo0003" not in tylko_block


def test_historia_stronicuje_w_html(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    panel.import_sample("bez-znalezisk.json", project_id)

    strona = panel.get("/przebiegi?na_stronie=1").text
    assert "Strona 1 z 2" in strona
    assert "Następna" in strona


def test_import_z_formularza_prowadzi_do_przebiegu(panel: Panel) -> None:
    from conftest import load_sample_bytes

    project_id = panel.create_project()
    response = panel.post(
        "/import",
        data={"project_id": project_id},
        files={"file": ("demo.json", load_sample_bytes("demo-celowe-usterki.json"),
                        "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("?zaimportowano=1")

    strona = panel.get(response.headers["location"]).text
    assert "Raport zaimportowany." in strona


def test_powtorny_import_z_formularza_mowi_o_duplikacie(panel: Panel) -> None:
    from conftest import load_sample_bytes

    project_id = panel.create_project()
    plik = load_sample_bytes("demo-celowe-usterki.json")
    for _ in range(2):
        response = panel.post(
            "/import",
            data={"project_id": project_id},
            files={"file": ("demo.json", plik, "application/json")},
            follow_redirects=False,
        )
    assert response.headers["location"].endswith("?duplikat=1")
    assert "nie utworzył duplikatu" in panel.get(response.headers["location"]).text


def test_bledny_plik_w_formularzu_zostaje_na_stronie_importu(panel: Panel) -> None:
    project_id = panel.create_project()
    response = panel.post(
        "/import",
        data={"project_id": project_id},
        files={"file": ("zle.json", b"{nie json", "application/json")},
    )
    assert response.status_code == 422
    assert "Raport odrzucony" in response.text


def test_archiwizacja_projektu_z_formularza(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    response = panel.post(
        f"/projekty/{project_id}/archiwum", data={"archived": "1"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "zarchiwizowany" in panel.get("/projekty").text
    # Raport przetrwał archiwizację projektu.
    assert panel.get(f"/api/v1/projects/{project_id}/runs").json()["total"] == 1


def test_lista_projektow_prowadzi_do_ustawien(panel: Panel, demo: tuple[int, str]) -> None:
    """Bez tego linku ścieżkę repozytorium dało się ustawić tylko przez API.

    Strona ustawień była osiągalna wyłącznie ze szczegółów zadania, a zadania
    nie da się utworzyć, dopóki projekt nie ma ścieżki — błędne koło.
    """
    project_id, _ = demo
    strona = panel.get("/projekty").text
    assert f'href="/projekty/{project_id}"' in strona


def test_lista_projektow_mowi_czego_brakuje_do_kontroli(
    panel: Panel, demo: tuple[int, str], git_repo: GitRepo
) -> None:
    project_id, _ = demo
    strona = panel.get("/projekty").text
    assert "tylko raporty" in strona
    assert "ścieżki repozytorium" in strona

    panel.patch_json(f"/api/v1/projects/{project_id}", {"repo_path": str(git_repo.path)})
    strona = panel.get("/projekty").text
    # Ścieżka jest, więc brakuje już tylko polityki — i tak ma być napisane.
    assert "ścieżki repozytorium" not in strona
    assert "profilu polityki" in strona


def test_nieznana_strona_ma_czytelny_blad(panel: Panel) -> None:
    response = panel.get("/projekty/999/przebiegi/xyz")
    assert response.status_code == 404
    assert "nie znam projektu" in response.text
