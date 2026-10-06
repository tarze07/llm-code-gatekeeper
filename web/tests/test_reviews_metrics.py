"""Oceny znalezisk, incydenty i metryki (etap 5)."""

from __future__ import annotations

from conftest import Panel


def fingerprint_of(panel: Panel, project_id: int, run_id: str, gate: str = "G3.secrets") -> str:
    report = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]
    target = next(g for g in report["gates"] if g["gate"] == gate)
    return str(target["findings"][0]["fingerprint"])


def test_ocena_nie_zmienia_decyzji_przebiegu(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    fingerprint = fingerprint_of(panel, project_id, run_id)
    przed = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]

    response = panel.post_json(
        f"/api/v1/projects/{project_id}/runs/{run_id}/findings/{fingerprint}/reviews",
        {"verdict": "false_positive", "author": "operator", "note": "to plik przykładowy"},
    )

    assert response.status_code == 201
    po = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]
    assert po == przed, "ocena nie ma prawa zmienić zapisanego raportu"


def test_zmiana_oceny_dopisuje_historie(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    fingerprint = fingerprint_of(panel, project_id, run_id)
    baza = f"/api/v1/projects/{project_id}/runs/{run_id}/findings/{fingerprint}/reviews"
    panel.post_json(baza, {"verdict": "false_positive", "author": "a"})
    panel.post_json(baza, {"verdict": "true_positive", "author": "b"})

    strona = panel.get(
        f"/projekty/{project_id}/przebiegi/{run_id}/znaleziska/{fingerprint}"
    ).text

    assert "Historia ocen (2)" in strona
    # Aktualna jest ostatnia, nie „jakaś".
    assert strona.index("Aktualna ocena") < strona.index("Historia ocen")
    assert "potwierdzony problem" in strona


def test_ocena_nieznanego_znaleziska_konczy_sie_404(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    response = panel.post_json(
        f"/api/v1/projects/{project_id}/runs/{run_id}/findings/nieistnieje/reviews",
        {"verdict": "true_positive"},
    )
    assert response.status_code == 404


def test_nieznana_ocena_jest_odrzucona(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    fingerprint = fingerprint_of(panel, project_id, run_id)
    response = panel.post_json(
        f"/api/v1/projects/{project_id}/runs/{run_id}/findings/{fingerprint}/reviews",
        {"verdict": "moze"},
    )
    assert response.status_code == 422


def test_incydent_nie_zmienia_raportu(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    przed = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()["report"]

    response = panel.post_json(
        f"/api/v1/projects/{project_id}/runs/{run_id}/incidents", {"note": "rollback o 3 w nocy"}
    )

    assert response.status_code == 201
    po = panel.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()
    assert po["report"] == przed
    assert po["run"]["caused_incident"] is True
    assert "rollback" in panel.get(f"/projekty/{project_id}/przebiegi/{run_id}").text


def test_metryki_bez_ocen_mowia_brak_danych(panel: Panel, demo: tuple[int, str]) -> None:
    body = panel.get("/api/v1/metrics?days=3650").json()
    precyzja = next(m for m in body["metrics"] if m["name"] == "Precyzja bramki")
    escape = next(m for m in body["metrics"] if m["name"] == "Escape rate")

    assert precyzja["value"] is None and precyzja["display"] == "brak danych"
    # Zero incydentów i brak oznaczania incydentów to dwie różne rzeczy.
    assert escape["value"] is None and "incydent" in escape["note"]


def test_metryki_licza_wystapienia_i_problemy_osobno(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, _ = demo
    # Ten sam raport w drugim przebiegu: dwa wystąpienia, jeden problem.
    drugi = panel.import_sample("demo-celowe-usterki.json", panel.create_project("Kopia"))
    assert drugi.status_code == 201

    body = panel.get(f"/api/v1/metrics?days=3650&project_id={project_id}").json()
    reguly = {r["rule_id"]: r for r in body["rules"]}
    cross = reguly["tests.pass_on_pre_change_code"]

    assert cross["occurrences"] == 5
    assert cross["unique_findings"] == 5
    assert cross["judged"] == 0


def test_wielokrotna_zmiana_oceny_nie_zawyza_metryk(panel: Panel, demo: tuple[int, str]) -> None:
    project_id, run_id = demo
    fingerprint = fingerprint_of(panel, project_id, run_id)
    baza = f"/api/v1/projects/{project_id}/runs/{run_id}/findings/{fingerprint}/reviews"
    for verdict in ("true_positive", "false_positive", "true_positive"):
        panel.post_json(baza, {"verdict": verdict})

    body = panel.get(f"/api/v1/metrics?days=3650&project_id={project_id}").json()
    reguly = {r["rule_id"]: r for r in body["rules"]}
    secrets = reguly["secrets.found_in_diff"]

    # Trzy zmiany zdania to nadal jedno ocenione znalezisko.
    assert secrets["judged"] == 1
    assert secrets["true_positives"] == 1
    assert secrets["occurrences"] == 1


def test_te_same_fingerprinty_w_dwoch_projektach_sa_osobne(
    panel: Panel, demo: tuple[int, str]
) -> None:
    pierwszy, run_id = demo
    drugi = panel.create_project("Drugi projekt")
    panel.import_sample("demo-celowe-usterki.json", drugi)
    fingerprint = fingerprint_of(panel, pierwszy, run_id)

    panel.post_json(
        f"/api/v1/projects/{pierwszy}/runs/{run_id}/findings/{fingerprint}/reviews",
        {"verdict": "false_positive"},
    )

    ocenione_w_drugim = panel.get(f"/api/v1/metrics?days=3650&project_id={drugi}").json()
    reguly = {r["rule_id"]: r for r in ocenione_w_drugim["rules"]}
    # Ocena z pierwszego projektu nie ma prawa „ocenić" cudzego znaleziska.
    assert reguly["secrets.found_in_diff"]["judged"] == 0


def test_strona_metryk_dziala(panel: Panel, demo: tuple[int, str]) -> None:
    strona = panel.get("/metryki?dni=3650").text
    assert "Precyzja bramki" in strona
    assert "brak danych" in strona
    assert "Wystąpienia" in strona


def test_escape_rate_bez_mianownika_nie_znika_z_metryk(
    panel: Panel, demo: tuple[int, str]
) -> None:
    """Same BLOCK-i i jeden incydent: mianownik jest zerem, ale metryka
    ma się pokazać jako brak danych, a nie zniknąć."""
    project_id, run_id = demo
    panel.post_json(f"/api/v1/projects/{project_id}/runs/{run_id}/incidents", {"note": "awaria"})

    body = panel.get(f"/api/v1/metrics?days=3650&project_id={project_id}").json()
    escape = next(m for m in body["metrics"] if m["name"] == "Escape rate")

    assert escape["value"] is None
    assert "nie ma czego dzielić" in escape["note"]
