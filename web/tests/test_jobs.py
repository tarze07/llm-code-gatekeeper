"""Kolejka, nadzorca i realne uruchomienie kontroli.

Scenariusze odbioru 4–8 z `PLAN-WEB-UI.md` §9. Testy uruchamiają prawdziwy
silnik na prawdziwym repozytorium — z ograniczeniem do bramek G0, które nie
potrzebują zewnętrznych narzędzi, więc zestaw działa też na maszynie bez
semgrepa i bez sieci.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import GitRepo
from gatekeeper_core.core import container

from gatekeeper_web.config import Settings
from gatekeeper_web.jobs.spec import build_job_input
from gatekeeper_web.jobs.supervisor import (
    HANDSHAKE_START,
    HANDSHAKE_STOP,
    Supervisor,
    SupervisorBusy,
    remove_worker_containers,
    supervisor_running,
)
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


# ------------------------------------------------- procesy: POSIX i Windows


def test_zatrzymanie_nadzorcy_zatrzymuje_proces_przebiegu(panelownia: Panelownia) -> None:
    """`shutdown()` nie zostawia workera: łagodnie (SIGTERM / „stop"), potem siłą."""
    job_id = panelownia.zlec()
    panelownia.supervisor.acquire_lock()
    panelownia.supervisor.tick()
    active = panelownia.supervisor.active
    assert active is not None

    panelownia.supervisor.shutdown()

    assert active.process.poll() is not None, "proces przebiegu przeżył nadzorcę"
    assert active.job is None, "uchwyt joba workera nie został zamknięty"
    assert panelownia.supervisor.active is None
    job = panelownia.queue.get(job_id)
    assert job is not None and job.is_terminal
    assert not supervisor_running(panelownia.settings)


def test_eskalacja_zabija_proces_przebiegu(panelownia: Panelownia) -> None:
    """Druga faza anulowania: SIGKILL grupy (POSIX) albo TerminateJobObject (Windows)."""
    job_id = panelownia.zlec()
    panelownia.supervisor.tick()
    active = panelownia.supervisor.active
    assert active is not None
    panelownia.queue.request_cancel(job_id)

    panelownia.supervisor._terminate(active, escalate=True)
    state = panelownia.pracuj(job_id, limit_s=60.0)

    assert active.process.poll() is not None
    assert active.killed
    # Zabity przebieg nie ma decyzji — chyba że raport zdążył się zapisać.
    assert state in ("cancelled", "completed")


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object to mechanizm Windows")
def test_smierc_nadzorcy_zabija_proces_przebiegu_windows(panelownia: Panelownia) -> None:
    """Jedyny uchwyt joba workera trzyma nadzorca. Jego śmierć (tu: zamknięcie
    uchwytu, jak przy TerminateProcess nadzorcy) zabija całe drzewo przebiegu."""
    from gatekeeper_core.core.winjob import process_alive

    panelownia.zlec()
    panelownia.supervisor.tick()
    active = panelownia.supervisor.active
    assert active is not None and active.job is not None
    pid = active.process.pid

    active.job.close()

    active.process.wait(timeout=10)
    assert not process_alive(pid)


def test_worker_bez_potwierdzenia_joba_niczego_nie_uruchamia(
    panelownia: Panelownia,
) -> None:
    """`--job-handshake`: bez `start` na stdin worker kończy się, zanim cokolwiek
    uruchomi — proces spoza joba nadzorcy przeżyłby anulowanie."""
    job_id = panelownia.zlec()
    claimed = panelownia.queue.claim("test:1", 30.0)
    assert claimed is not None and claimed.id == job_id

    for wejscie in (b"", b"cokolwiek\n"):
        wynik = subprocess.run(
            [sys.executable, "-m", "gatekeeper_web.jobs.worker",
             "--state-dir", str(panelownia.settings.state_dir),
             "--job-id", str(job_id), "--job-handshake"],
            input=wejscie, capture_output=True, timeout=60, check=False,
        )
        assert wynik.returncode == 2, wynik.stderr.decode("utf-8", errors="replace")

    job = panelownia.queue.get(job_id)
    assert job is not None and job.state == "preparing"
    assert "prepared" not in [e.kind for e in panelownia.queue.events(job_id)]


def _czekaj_na_stop(worker: object) -> bool:
    deadline = time.monotonic() + 5
    while not worker._stop_requested and time.monotonic() < deadline:  # type: ignore[attr-defined]
        time.sleep(0.02)
    return bool(worker._stop_requested)  # type: ignore[attr-defined]


def test_stop_od_nadzorcy_dziala_jak_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    import gatekeeper_web.jobs.worker as worker

    monkeypatch.setattr(worker, "_stop_requested", False)
    czytaj, pisz = os.pipe()
    try:
        os.write(pisz, HANDSHAKE_START)
        assert worker.wait_for_job_handshake(czytaj)
        time.sleep(0.2)
        assert not worker._stop_requested, "samo `start` nie może zatrzymać przebiegu"

        os.write(pisz, b"cos innego\n")
        time.sleep(0.2)
        assert not worker._stop_requested, "nieznany komunikat nie zatrzymuje przebiegu"

        os.write(pisz, HANDSHAKE_STOP)
        assert _czekaj_na_stop(worker)
    finally:
        os.close(pisz)
    os.close(czytaj)  # wątek skończył czytać po `stop`


def test_koniec_kanalu_zatrzymuje_przebieg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nadzorca zniknął (koniec strumienia) = prośba o zatrzymanie."""
    import gatekeeper_web.jobs.worker as worker

    monkeypatch.setattr(worker, "_stop_requested", False)
    czytaj, pisz = os.pipe()
    os.write(pisz, HANDSHAKE_START)
    assert worker.wait_for_job_handshake(czytaj)
    os.close(pisz)

    assert _czekaj_na_stop(worker)
    os.close(czytaj)


def test_blokada_nadzorcy_widac_z_innego_procesu(panelownia: Panelownia) -> None:
    """Pulpit pyta o nadzorcę z procesu serwera — blokada musi być widoczna
    między procesami na każdym systemie, a plik czytelny mimo blokady."""
    sprawdz = [
        sys.executable, "-c",
        "import sys; from pathlib import Path;"
        "from gatekeeper_web.config import Settings;"
        "from gatekeeper_web.jobs.supervisor import supervisor_running;"
        "print(supervisor_running(Settings(state_dir=Path(sys.argv[1]))))",
        str(panelownia.settings.state_dir),
    ]

    def z_zewnatrz() -> str:
        return subprocess.run(
            sprawdz, capture_output=True, text=True, timeout=60, check=True
        ).stdout.strip()

    assert z_zewnatrz() == "False"
    panelownia.supervisor.acquire_lock()
    try:
        assert z_zewnatrz() == "True"
        tresc = panelownia.settings.supervisor_lock_path.read_text(encoding="utf-8")
        assert tresc.strip() == panelownia.supervisor.owner
    finally:
        panelownia.supervisor.release_lock()
    assert z_zewnatrz() == "False"
    # Zwolnioną blokadę może wziąć następca.
    nastepca = Supervisor(panelownia.settings)
    nastepca.acquire_lock()
    nastepca.release_lock()


FAKE_ENGINE = '''
import json, sys
from pathlib import Path

LOG = Path(__file__).with_name("wywolania.jsonl")
WLASCICIELE = {"c1": "4242-aaa", "c2": "42424-bbb", "c3": "4242-ccc", "c4": "1-4242-x"}
args = sys.argv[1:]
with LOG.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "ps":
    filtry = [args[i + 1] for i, a in enumerate(args) if a == "--filter"]
    wybrane = list(WLASCICIELE)
    for f in filtry:
        if f.startswith("label=gatekeeper.owner="):
            owner = f.split("=", 2)[2]
            wybrane = [c for c in wybrane if WLASCICIELE[c] == owner]
    print("\\n".join(wybrane))
elif args[0] == "inspect":
    print("\\n".join(WLASCICIELE[c] for c in args if c in WLASCICIELE))
'''


def test_kontenery_zabitego_workera_sa_usuwane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Zabity worker nie usunie kontenerów swoich bramek. Nadzorca usuwa te,
    których właściciel zaczyna się od `<pid workera>-` — i tylko te."""
    skrypt = tmp_path / "silnik.py"
    skrypt.write_text(FAKE_ENGINE, encoding="utf-8")
    if sys.platform == "win32":
        silnik = tmp_path / "docker.cmd"
        silnik.write_text(f'@"{sys.executable}" "{skrypt}" %*\r\n', encoding="utf-8")
    else:
        silnik = tmp_path / "docker"
        silnik.write_text(f"#!{sys.executable}\n" + FAKE_ENGINE, encoding="utf-8")
        silnik.chmod(0o755)
    monkeypatch.setenv("GATEKEEPER_CONTAINER_ENGINE", str(silnik))
    monkeypatch.setenv("GATEKEEPER_SANDBOX", "container")
    # `engine()` pamięta pierwszy wynik — wcześniejszy test mógł już zapytać.
    container.engine.cache_clear()
    request.addfinalizer(container.engine.cache_clear)

    remove_worker_containers(4242)

    wywolania = [
        json.loads(line)
        for line in (tmp_path / "wywolania.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    usuniete = [c for args in wywolania if args[0] == "rm" for c in args[2:]]
    assert sorted(usuniete) == ["c1", "c3"]


def test_bez_backendu_kontenerow_nie_wola_silnika(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATEKEEPER_CONTAINER_ENGINE", str(tmp_path / "nie-istnieje"))
    monkeypatch.setenv("GATEKEEPER_SANDBOX", "bwrap")

    remove_worker_containers(4242)  # nie rzuca, nie szuka silnika
