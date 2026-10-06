"""API etapów 2–4: zakres, kolejka, postęp, anulowanie, ponowienie."""

from __future__ import annotations

from conftest import Panel


def test_lista_referencji_pochodzi_z_repozytorium(panel: Panel, gotowy_projekt: int) -> None:
    body = panel.get(f"/api/v1/projects/{gotowy_projekt}/refs").json()
    assert {ref["name"] for ref in body["refs"]} >= {"main", "praca"}


def test_podglad_pokazuje_dokladny_zakres(panel: Panel, gotowy_projekt: int) -> None:
    response = panel.post_json(
        f"/api/v1/projects/{gotowy_projekt}/preview", {"base": "main", "head": "HEAD"}
    )
    preview = response.json()["preview"]

    assert response.status_code == 200
    assert preview["effective_files"] == 1
    assert preview["merge_base"]
    assert "src/app.py" in preview["paths"]


def test_podglad_odrzuca_wstrzykniecie_argumentu_gita(panel: Panel, gotowy_projekt: int) -> None:
    response = panel.post_json(
        f"/api/v1/projects/{gotowy_projekt}/preview", {"base": "--upload-pack=touch /tmp/x"}
    )
    assert response.status_code == 422
    assert "niedozwolona nazwa wersji Git" in response.json()["detail"]


def test_projekt_bez_repozytorium_nie_uruchomi_kontroli(panel: Panel) -> None:
    project_id = panel.create_project("Bez repo")
    response = panel.post_json("/api/v1/jobs", {"project_id": project_id, "base": "main"})
    assert response.status_code == 409
    assert "repozytorium" in response.json()["detail"]


def test_projekt_bez_aktywnej_polityki_nie_uruchomi_kontroli(
    panel: Panel, git_repo: object
) -> None:
    project_id = panel.create_project("Bez polityki")
    profile = panel.post_json("/api/v1/policies", {"name": "Pusty"}).json()["profile"]
    panel.patch_json(
        f"/api/v1/projects/{project_id}",
        {"repo_path": str(git_repo.path), "policy_profile_id": profile["id"]},  # type: ignore[attr-defined]
    )
    response = panel.post_json("/api/v1/jobs", {"project_id": project_id, "base": "main"})
    assert response.status_code == 409
    assert "aktywnej wersji" in response.json()["detail"]


def test_zlecenie_kontroli_odpowiada_202(panel: Panel, gotowy_projekt: int) -> None:
    response = panel.post_json("/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"})

    assert response.status_code == 202
    assert response.headers["location"].startswith("/api/v1/jobs/")
    job = response.json()["job"]
    assert job["state"] == "queued"
    # Zadanie w kolejce nie ma decyzji polityki i tego nie udaje.
    assert job["has_result"] is False and job["run_id"] is None


def test_zamrozone_wejscie_jest_widoczne(panel: Panel, gotowy_projekt: int) -> None:
    job_id = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main", "gates": ["G0.scope"]}
    ).json()["job"]["id"]

    body = panel.get(f"/api/v1/jobs/{job_id}").json()

    assert body["input"]["gates"] == ["G0.scope"]
    assert body["input"]["merge_base"]
    assert body["input"]["policy_hash"]
    assert body["input"]["policy_revision"] == 1


def test_podwojne_klikniecie_nie_zleca_dwoch_analiz(panel: Panel, gotowy_projekt: int) -> None:
    payload = {"project_id": gotowy_projekt, "base": "main", "idempotency_key": "jedno"}
    first = panel.post_json("/api/v1/jobs", payload).json()
    second = panel.post_json("/api/v1/jobs", payload).json()

    assert first["job"]["id"] == second["job"]["id"]
    assert first["created"] is True and second["created"] is False
    assert panel.get(f"/api/v1/projects/{gotowy_projekt}/jobs").json()["total"] == 1


def test_anulowanie_jest_idempotentne(panel: Panel, gotowy_projekt: int) -> None:
    job_id = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]["id"]

    first = panel.post_json(f"/api/v1/jobs/{job_id}/cancel", {}).json()
    second = panel.post_json(f"/api/v1/jobs/{job_id}/cancel", {}).json()

    assert first["job"]["state"] == "cancelled" and first["changed"] is True
    assert second["job"]["state"] == "cancelled" and second["changed"] is False


def test_ponowienie_tworzy_nowe_zadanie(panel: Panel, gotowy_projekt: int) -> None:
    original = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]

    retried = panel.post_json(f"/api/v1/jobs/{original['id']}/retry", {"mode": "same_scope"})

    assert retried.status_code == 202
    job = retried.json()["job"]
    assert job["id"] != original["id"]
    assert job["retry_of"] == original["id"]


def test_ponowienie_najnowszej_wersji_przelicza_zakres(panel: Panel, gotowy_projekt: int) -> None:
    original = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]

    response = panel.post_json(f"/api/v1/jobs/{original['id']}/retry", {"mode": "latest"})

    assert response.status_code == 202
    assert response.json()["mode"] == "latest"


def test_nieznany_tryb_ponowienia_jest_odrzucony(panel: Panel, gotowy_projekt: int) -> None:
    job_id = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]["id"]
    assert panel.post_json(f"/api/v1/jobs/{job_id}/retry", {"mode": "cokolwiek"}).status_code == 422


def test_zdarzenia_sa_przyrostowe(panel: Panel, gotowy_projekt: int) -> None:
    job_id = panel.post_json(
        "/api/v1/jobs", {"project_id": gotowy_projekt, "base": "main"}
    ).json()["job"]["id"]

    wszystkie = panel.get(f"/api/v1/jobs/{job_id}/events").json()
    puste = panel.get(f"/api/v1/jobs/{job_id}/events?after={wszystkie['last_seq']}").json()

    assert wszystkie["events"][0]["kind"] == "queued"
    assert puste["events"] == []
    assert puste["state"] == "queued"


def test_nieznane_zadanie_daje_404(panel: Panel) -> None:
    assert panel.get("/api/v1/jobs/98765").status_code == 404


def test_diagnostyka_srodowiska(panel: Panel) -> None:
    body = panel.get("/api/v1/environment").json()

    assert "isolation" in body and "tools" in body
    assert len(body["gates"]) >= 11
    # Lista bramek pochodzi z entry pointów, nie z listy wpisanej w panelu.
    assert any(gate["id"] == "G0.scope" for gate in body["gates"])


def test_zla_sciezka_repozytorium_jest_odrzucona(panel: Panel) -> None:
    project_id = panel.create_project("Zły katalog")
    response = panel.patch_json(f"/api/v1/projects/{project_id}", {"repo_path": "/etc"})
    assert response.status_code == 422
    assert "poza katalogami dozwolonymi" in response.json()["detail"]
