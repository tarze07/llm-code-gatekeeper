"""Nadzorca kolejki.

Jeden proces, jedna instancja, jedno zadanie naraz (równoległość *wewnątrz*
przebiegu zostaje bez zmian). Odpowiada za cztery rzeczy, których nie da się
zrobić z procesu serwera HTTP (PLAN-WEB-UI.md §5):

1. **przejęcie zadania** — atomowo, z dzierżawą, żeby dwie instancje nie
   wzięły tego samego;
2. **uruchomienie osobnego procesu** przebiegu, w nowej sesji, żeby dało się
   zabić całą jego grupę razem z narzędziami bramek. Na Windows proces trafia
   do własnego Job Object (`KILL_ON_JOB_CLOSE`), *zanim* zacznie cokolwiek
   uruchamiać — patrz `_spawn_windows`;
3. **anulowanie** — SIGTERM z okresem karencji, potem SIGKILL grupy procesów.
   Zabicie samego rodzica nie wystarcza: workery bramek robią `setsid()`.
   Na Windows sygnałów nie ma: worker sam sprawdza flagę anulowania w bazie,
   a nadzorca dodatkowo pisze „stop" na jego stdin; eskalacja to
   `TerminateJobObject` — całe drzewo, łącznie z narzędziami bramek;
4. **odtworzenie po restarcie** — zadanie po zmarłym nadzorcy jest
   `interrupted`, a nie wznawiane po cichu. Jeśli raport zdążył się zapisać,
   zadanie zostaje uczciwie domknięte jako `completed`.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from gatekeeper_core.core import container
from gatekeeper_core.core.runner import SandboxUnavailable, backend
from gatekeeper_core.core.winjob import Job as WinJob

from ..config import Settings
from ..console import utf8_console
from ..storage import Database, Job, JobQueue, Repository
from .locking import FileLock, is_locked

_WINDOWS = sys.platform == "win32"

#: Dzierżawa jest krótka, a heartbeat częsty: im krócej, tym szybciej po
#: awarii wiadomo, że zadania nikt nie pilnuje.
LEASE_S = 30.0
HEARTBEAT_S = 5.0
POLL_S = 1.0

#: Ile czekamy, aż worker sam się zatrzyma po SIGTERM, zanim zabijemy grupę.
CANCEL_GRACE_S = 20.0

#: Uzgodnienie z workerem na Windows: stdin workera, jedna linia na komunikat.
#: `start` idzie dopiero po przypisaniu workera do Job Object; `stop` to
#: odpowiednik SIGTERM. Koniec strumienia (nadzorca zniknął) worker traktuje
#: jak `stop`.
HANDSHAKE_START = b"start\n"
HANDSHAKE_STOP = b"stop\n"


class SupervisorBusy(RuntimeError):
    """Inna instancja nadzorcy już pracuje na tym katalogu stanu."""


@dataclass
class ActiveRun:
    job_id: int
    process: subprocess.Popen[bytes]
    started_at: float
    terminate_sent_at: float | None = None
    #: Windows: job workera; zamknięcie uchwytu zabija całe drzewo.
    job: WinJob | None = None
    #: Proces zabity siłą — bramki mogły nie zdążyć usunąć swoich kontenerów.
    killed: bool = False


class Supervisor:
    def __init__(self, settings: Settings, poll_s: float = POLL_S) -> None:
        self.settings = settings
        self.poll_s = poll_s
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.database = Database(settings.db_path)
        self.queue = JobQueue(self.database)
        self.repository = Repository(self.database)
        self.active: ActiveRun | None = None
        self._lock = FileLock(settings.supervisor_lock_path)
        self._last_heartbeat = 0.0

    # ------------------------------------------------------- pojedyncza instancja

    def acquire_lock(self) -> None:
        self.settings.ensure_state_dir()
        try:
            acquired = self._lock.try_acquire(self.owner)
        except OSError as exc:
            raise SupervisorBusy(
                f"nie da się zablokować katalogu {self.settings.state_dir}: {exc}"
            ) from exc
        if not acquired:
            raise SupervisorBusy(f"nadzorca już działa na katalogu {self.settings.state_dir}")

    def release_lock(self) -> None:
        self._lock.release()

    # ------------------------------------------------------------- odtwarzanie

    def recover(self) -> list[int]:
        """Rozlicza zadania pozostawione przez poprzedniego nadzorcę."""
        stale = self.queue.reclaim_expired()
        for job_id in stale:
            # Raport mógł się zapisać tuż przed zgonem procesu. Wtedy „przerwane"
            # byłoby kłamstwem — wynik istnieje i da się go pokazać.
            row = self.repository.report_for_job(job_id)
            if row is not None:
                self.queue.transition(
                    job_id,
                    "completed",
                    ("interrupted",),
                    run_id=row.run_id,
                    message="raport odnaleziony po restarcie nadzorcy",
                )
        return stale

    # -------------------------------------------------------------- pętla

    def run_forever(  # pragma: no cover - pętla procesu nadzorcy
        self, should_stop: Callable[[], bool] | None = None
    ) -> None:
        self.acquire_lock()
        try:
            self.recover()
            while not (should_stop and should_stop()):
                self.tick()
                time.sleep(self.poll_s)
        finally:
            self.shutdown()

    def tick(self) -> Job | None:
        """Jeden obrót pętli. Zwraca zadanie, którym się teraz zajmuje."""
        if self.active is not None:
            return self._supervise_active()
        self.queue.reclaim_expired()
        job = self.queue.claim(self.owner, LEASE_S)
        if job is None:
            return None
        self._spawn(job)
        return job

    def _spawn(self, job: Job) -> None:
        command = [
            sys.executable,
            "-m",
            "gatekeeper_web.jobs.worker",
            "--state-dir",
            str(self.settings.state_dir),
            "--job-id",
            str(job.id),
        ]
        if _WINDOWS:
            self._spawn_windows(job, command)
            return
        # Argumenty jako lista, bez powłoki; własna sesja, żeby dało się zabić
        # całą grupę procesów razem z narzędziami bramek.
        process = subprocess.Popen(command, start_new_session=True)
        self._started(job, ActiveRun(job_id=job.id, process=process, started_at=time.monotonic()))

    def _spawn_windows(self, job: Job, command: list[str]) -> None:
        """Windows: worker czeka na `start`, dopóki nie siedzi w naszym jobie.

        Przypisanie do joba da się zrobić dopiero po starcie procesu. Bez
        uzgodnienia worker zdążyłby uruchomić bramki poza jobem, a te
        przeżyłyby eskalację anulowania i śmierć nadzorcy. Z `--job-handshake`
        worker niczego nie zaczyna, dopóki nie przeczyta `start` ze stdin;
        potomkowie dziedziczą członkostwo w jobie od chwili przypisania.
        """
        process = subprocess.Popen(
            [*command, "--job-handshake"],
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined,unused-ignore]
        )
        winjob: WinJob | None = None
        try:
            winjob = WinJob()
            winjob.assign(process.pid)
            assert process.stdin is not None
            process.stdin.write(HANDSHAKE_START)
            process.stdin.flush()
        except OSError as exc:
            # Fail-closed: bez joba nie ma gwarancji sprzątnięcia drzewa
            # procesów, więc przebieg w ogóle nie rusza.
            process.kill()
            process.wait()
            if winjob is not None:
                winjob.close()
            self.queue.transition(
                job.id, "failed", ("preparing", "running", "cancelling"),
                error=f"nie da się zamknąć procesu przebiegu w Job Object: {exc}",
                message="proces przebiegu bez kontroli nad drzewem procesów — nie uruchomiono",
            )
            return
        self._started(
            job,
            ActiveRun(job_id=job.id, process=process, started_at=time.monotonic(), job=winjob),
        )

    def _started(self, job: Job, active: ActiveRun) -> None:
        self.queue.set_worker_pid(job.id, active.process.pid)
        self.queue.append_event(job.id, "worker_started", message=f"proces {active.process.pid}")
        self.active = active
        self._last_heartbeat = time.monotonic()

    def _supervise_active(self) -> Job | None:
        active = self.active
        assert active is not None
        now = time.monotonic()

        if now - self._last_heartbeat >= HEARTBEAT_S:
            self.queue.heartbeat(active.job_id, self.owner, LEASE_S)
            self._last_heartbeat = now

        job = self.queue.get(active.job_id)
        if job is not None and job.cancel_requested and active.terminate_sent_at is None:
            self._terminate(active, escalate=False)
        elif (
            active.terminate_sent_at is not None
            and now - active.terminate_sent_at >= CANCEL_GRACE_S
        ):
            self._terminate(active, escalate=True)

        if active.process.poll() is None:
            return job

        return self._finish(active)

    def _terminate(self, active: ActiveRun, escalate: bool) -> None:
        if escalate:
            active.killed = True
        if active.job is not None:
            _terminate_windows(active, escalate)
        else:
            sig = signal.SIGKILL if escalate else signal.SIGTERM  # type: ignore[attr-defined,unused-ignore]
            # Proces mógł się właśnie sam zakończyć — to nie jest błąd.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(active.process.pid), sig)  # type: ignore[attr-defined,unused-ignore]
        if not escalate:
            active.terminate_sent_at = time.monotonic()
            self.queue.append_event(
                active.job_id,
                "cancelling",
                message=(
                    "poproszono proces przebiegu o zatrzymanie"
                    if active.job is not None
                    else "wysłano SIGTERM do procesu przebiegu"
                ),
            )
        else:
            self.queue.append_event(
                active.job_id,
                "cancelling",
                message="proces nie zatrzymał się w czasie karencji — zabito grupę procesów",
            )

    def _finish(self, active: ActiveRun) -> Job | None:
        code = active.process.returncode
        self.active = None
        _release_worker(active, code)
        self.queue.set_worker_pid(active.job_id, None)
        job = self.queue.get(active.job_id)
        if job is None:
            return None
        if job.is_terminal:
            return job

        # Worker zniknął, nie rozliczywszy zadania. Raport mógł się zapisać.
        row = self.repository.report_for_job(job.id)
        if row is not None:
            self.queue.transition(
                job.id, "completed", ("preparing", "running", "cancelling"),
                run_id=row.run_id, message="raport zapisany mimo awarii procesu",
            )
        elif job.cancel_requested:
            self.queue.transition(
                job.id, "cancelled", ("preparing", "running", "cancelling"),
                message="proces przebiegu zatrzymany na żądanie",
            )
        else:
            self.queue.transition(
                job.id, "failed", ("preparing", "running", "cancelling"),
                error=f"proces przebiegu zakończył się kodem {code} bez zapisania wyniku",
                message="proces przebiegu zniknął",
            )
        return self.queue.get(job.id)

    def shutdown(self) -> None:
        """Zatrzymanie nadzorcy nie zostawia sierot."""
        if self.active is not None:
            active = self.active
            try:
                self._terminate(active, escalate=False)
                deadline = time.monotonic() + CANCEL_GRACE_S
                while active.process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.2)
                if active.process.poll() is None:  # pragma: no cover
                    self._terminate(active, escalate=True)
                    active.process.wait(timeout=5)
                self._finish(active)
            finally:
                # Nawet gdy rozliczenie w bazie się nie powiodło: zamknięcie
                # joba zabija drzewo przebiegu (KILL_ON_JOB_CLOSE).
                if active.job is not None:
                    active.job.close()
        self.release_lock()


def _terminate_windows(active: ActiveRun, escalate: bool) -> None:
    """Windows: „stop" na stdin (worker sprawdza też flagę w bazie), potem job."""
    assert active.job is not None
    if escalate:
        active.job.terminate()
        return
    stdin = active.process.stdin
    if stdin is not None:
        # Worker mógł już skończyć i zamknąć swój koniec rury.
        with suppress(OSError, ValueError):
            stdin.write(HANDSHAKE_STOP)
            stdin.flush()


def _release_worker(active: ActiveRun, code: int | None) -> None:
    """Domyka zasoby po procesie przebiegu, który już się zakończył."""
    if active.job is not None:
        # Proces główny się zakończył, ale potomkowie mogli przeżyć (np.
        # narzędzie odłączone od bramki) — job zabija resztę drzewa.
        try:
            with suppress(OSError):
                active.job.terminate()
        finally:
            active.job.close()
            active.job = None
    if active.process.stdin is not None:
        with suppress(OSError, ValueError):
            active.process.stdin.close()
    if active.killed or code != 0:
        remove_worker_containers(active.process.pid)


def remove_worker_containers(worker_pid: int) -> None:
    """Usuwa kontenery bramek po workerze, który nie posprzątał sam.

    Bramka sprząta swoje kontenery w `Running.close()` (execution.py), ale
    zabity worker tego nie zrobi, a zabicie klienta `docker run` nie zatrzymuje
    kontenera. Kontenery bramek mają etykietę właściciela
    `gatekeeper.owner=<pid procesu run_wave>-<losowy sufiks>`; `run_wave`
    biegnie w procesie workera, więc prefiks `<pid workera>-` wskazuje
    dokładnie jego bramki. Silnik nie filtruje etykiet po prefiksie, więc
    właścicieli wybieramy sami i każdego usuwamy funkcją z core.

    Wołane zaraz po śmierci workera, zanim jego PID zdąży przejść na inny
    proces. Ostatnia linia obrony i tak istnieje: każdy kontener ma
    `timeout -s KILL` niewiele ponad limit bramki.
    """
    try:
        if backend() != "container":
            return
    except SandboxUnavailable:
        return
    found = container.engine()
    if found is None:
        return
    prefix = f"{worker_pid}-"
    try:
        listed = subprocess.run(
            [found, "ps", "-aq", "--filter", "label=gatekeeper=1",
             "--filter", f"label={container.OWNER_LABEL}"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        ids = listed.stdout.split()
        if not ids:
            return
        inspected = subprocess.run(
            [found, "inspect", "--format",
             f'{{{{index .Config.Labels "{container.OWNER_LABEL}"}}}}', *ids],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    owners = {line.strip() for line in inspected.stdout.splitlines()}
    for owner in sorted(o for o in owners if o.startswith(prefix)):
        container.remove_containers(owner=owner)


def supervisor_running(settings: Settings) -> bool:
    """Czy ktoś trzyma blokadę nadzorcy dla tego katalogu stanu."""
    try:
        return is_locked(settings.supervisor_lock_path)
    except OSError:  # pragma: no cover
        return False


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - proces nadzorcy
    """Punkt wejścia procesu nadzorcy (`python -m gatekeeper_web.jobs.supervisor`)."""
    import argparse

    parser = argparse.ArgumentParser(description="Nadzorca kolejki panelu")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--poll", type=float, default=POLL_S)
    parser.add_argument(
        "--stop-file",
        help="Pojawienie się tego pliku = łagodne zatrzymanie (Windows nie ma SIGTERM)",
    )
    args = parser.parse_args(argv)
    utf8_console()

    settings = Settings(state_dir=Path(args.state_dir).expanduser())
    supervisor = Supervisor(settings, poll_s=args.poll)
    stop_file = Path(args.stop_file) if args.stop_file else None
    stopping = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    def _should_stop() -> bool:
        return stopping or (stop_file is not None and stop_file.exists())

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if _WINDOWS:
        # Ctrl+Break z konsoli: proces w osobnej grupie nie dostaje Ctrl+C.
        signal.signal(signal.SIGBREAK, _stop)  # type: ignore[attr-defined,unused-ignore]
    try:
        supervisor.run_forever(should_stop=_should_stop)
    except SupervisorBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
