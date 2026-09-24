"""Kolejka, nadzorca i realne uruchomienie kontroli.

Scenariusze odbioru 4–8 z `PLAN-WEB-UI.md` §9. Testy uruchamiają prawdziwy
silnik na prawdziwym repozytorium — z ograniczeniem do bramek G0, które nie
potrzebują zewnętrznych narzędzi, więc zestaw działa też na maszynie bez
semgrepa i bez sieci.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import GitRepo

from gatekeeper_web.config import Settings
from gatekeeper_web.jobs.spec import build_job_input
from gatekeeper_web.jobs.supervisor import Supervisor, SupervisorBusy
from gatekeeper_web.services.repos import preview_scope, resolve_repo_path
from gatekeeper_web.storage import (
    Database,
    JobQueue,
    PolicyStore,
    Repository,
)

POLICY = """
version: 1
thresholds:
  diff.effective_files:
    max: 50
"""

GATES = ("G0.provenance", "G0.scope")


class Panelownia:
    """Minimalny panel bez HTTP: baza, projekt, profil, kolejka, nadzorca."""

    def __init__(self, settings: Settings, repo: GitRepo) -> None:
        settings.ensure_state_dir()
        self.settings = settings
        self.database = Database(settings.db_path)
        self.repository = Repository(self.database)
        self.policies = PolicyStore(self.database)
        self.queue = JobQueue(self.database)
        self.supervisor = Supervisor(settings, poll_s=0.05)

        profile = self.policies.create_profile("Domyślny")
        self.revision = self.policies.activate(
            self.policies.create_revision(profile.id, POLICY, author="test").id, author="test"
        )
        project = self.repository.create_project("Testowy")
        self.project = self.repository.update_project(
            project.id,
            repo_path=str(resolve_repo_path(str(repo.path), settings.allowed_repo_roots)),
            policy_profile_id=profile.id,
        )
        self.repo = repo

    def zlec(self, gates: tuple[str, ...] = GATES, **kwargs: object) -> int:
        preview = preview_scope(Path(self.project.repo_path or ""), "main", "HEAD")
        payload = build_job_input(self.project, self.revision, preview, gates=gates)
        job, _created = self.queue.enqueue(self.project.id, payload, **kwargs)  # type: ignore[arg-type]
        return job.id

    def pracuj(self, job_id: int, limit_s: float = 120.0) -> str:
        """Kręci pętlą nadzorcy aż zadanie się rozliczy."""
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            self.supervisor.tick()
            job = self.queue.get(job_id)
            assert job is not None
            if job.is_terminal:
                return job.state
            time.sleep(0.05)
        raise AssertionError(f"zadanie {job_id} nie skończyło się w {limit_s}s")


@pytest.fixture
def panelownia(settings: Settings, git_repo: GitRepo) -> Panelownia:
    return Panelownia(settings, git_repo)


def test_przebieg_z_kolejki_konczy_sie_zapisanym_raportem(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()

    assert panelownia.pracuj(job_id) == "completed"

    job = panelownia.queue.get(job_id)
    assert job is not None and job.run_id
    row = panelownia.repository.get_report(panelownia.project.id, job.run_id)
    assert row is not None
    # Raport z panelu nie udaje zaimportowanego.
    assert row.origin == "engine"
    assert row.job_id == job_id
    assert {g["gate"] for g in (row.payload or {})["gates"]} == set(GATES)


def test_postep_melduje_kolejne_kontrole(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()
    panelownia.pracuj(job_id)

    events = panelownia.queue.events(job_id)
    kinds = [e.kind for e in events]
    assert "queued" in kinds and "preparing" in kinds and "running" in kinds
    finished = [e for e in events if e.kind == "gate_finished"]
    assert {e.gate for e in finished} == set(GATES)
    # „Ukończono N z M", a nie procent czasu.
    assert finished[-1].completed == finished[-1].total == len(GATES)


def test_zdarzenia_pobiera_sie_przyrostowo(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()
    panelownia.pracuj(job_id)

    wszystkie = panelownia.queue.events(job_id)
    ogon = panelownia.queue.events(job_id, after=wszystkie[2].seq)
    assert [e.seq for e in ogon] == [e.seq for e in wszystkie[3:]]


def test_ten_sam_klucz_idempotencji_nie_zleca_dwoch_analiz(panelownia: Panelownia) -> None:
    first = panelownia.zlec(idempotency_key="jedno-klikniecie")
    second = panelownia.zlec(idempotency_key="jedno-klikniecie")

    assert first == second
    assert panelownia.queue.count_jobs(panelownia.project.id) == 1


def test_anulowanie_w_kolejce_konczy_zadanie_od_razu(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()

    job = panelownia.queue.request_cancel(job_id)

    assert job.state == "cancelled"
    assert job.run_id is None
    # Anulowane zadanie nie ma decyzji polityki — i nie udaje, że ma.
    assert not job.has_result


def test_anulowanie_jest_idempotentne(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()
    panelownia.queue.request_cancel(job_id)
    again = panelownia.queue.request_cancel(job_id)
    assert again.state == "cancelled"


def test_anulowanie_w_trakcie_zatrzymuje_proces(panelownia: Panelownia) -> None:
    job_id = panelownia.zlec()
    panelownia.supervisor.tick()  # przejęcie i start procesu przebiegu
    assert panelownia.supervisor.active is not None
    process = panelownia.supervisor.active.process

    panelownia.queue.request_cancel(job_id)
    state = panelownia.pracuj(job_id, limit_s=60.0)

    assert state in ("cancelled", "completed")
    assert process.poll() is not None, "proces przebiegu nadal działa"
    if state == "cancelled":
        job = panelownia.queue.get(job_id)
        assert job is not None and job.run_id is None


def test_zadanie_po_zmarlym_nadzorcy_jest_przerwane_a_nie_wznowione(
    panelownia: Panelownia,
) -> None:
    job_id = panelownia.zlec()
    panelownia.queue.claim("inny-nadzorca:1", lease_s=-1.0)  # dzierżawa od razu wygasła

    stale = panelownia.supervisor.recover()

    assert job_id in stale
    job = panelownia.queue.get(job_id)
    assert job is not None
    assert job.state == "interrupted"
    # Brak fałszywego PASS: przerwane zadanie nie ma wyniku.
    assert job.run_id is None and not job.has_result


def test_odtwarzanie_domyka_zadanie_z_zapisanym_raportem(panelownia: Panelownia) -> None:
    """Proces mógł zginąć między zapisem raportu a zmianą stanu."""
    job_id = panelownia.zlec()
    panelownia.pracuj(job_id)
    job = panelownia.queue.get(job_id)
    assert job is not None and job.run_id

    # Cofamy zadanie do stanu „w toku" z wygasłą dzierżawą, raport zostaje.
    with panelownia.database.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET state = 'running', finished_at = NULL,"
            " lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job_id,),
        )

    panelownia.supervisor.recover()

    recovered = panelownia.queue.get(job_id)
    assert recovered is not None
    assert recovered.state == "completed"
    assert recovered.run_id == job.run_id


def test_druga_instancja_nadzorcy_nie_wystartuje(panelownia: Panelownia) -> None:
    panelownia.supervisor.acquire_lock()
    try:
        with pytest.raises(SupervisorBusy):
            Supervisor(panelownia.settings).acquire_lock()
    finally:
        panelownia.supervisor.release_lock()


def test_jedno_zadanie_naraz(panelownia: Panelownia) -> None:
    first = panelownia.zlec()
    second = panelownia.zlec()
    panelownia.supervisor.tick()

    # Drugie czeka: MVP nie mnoży kopii repozytorium ani zużycia pamięci.
    assert panelownia.queue.claim("inny", 30.0) is None
    assert panelownia.pracuj(first) == "completed"
    assert panelownia.queue.get(second) is not None
    assert panelownia.queue.get(second).state == "queued"  # type: ignore[union-attr]


def test_zle_wejscie_zadania_konczy_sie_awaria_a_nie_zawieszeniem(
    panelownia: Panelownia,
) -> None:
    job, _ = panelownia.queue.enqueue(panelownia.project.id, {"input_version": 999})

    assert panelownia.pracuj(job.id, limit_s=60.0) == "failed"
    failed = panelownia.queue.get(job.id)
    assert failed is not None and "nowszego panelu" in (failed.error or "")
