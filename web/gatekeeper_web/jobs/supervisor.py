"""Nadzorca kolejki.

Jeden proces, jedna instancja, jedno zadanie naraz (równoległość *wewnątrz*
przebiegu zostaje bez zmian). Odpowiada za cztery rzeczy, których nie da się
zrobić z procesu serwera HTTP (PLAN-WEB-UI.md §5):

1. **przejęcie zadania** — atomowo, z dzierżawą, żeby dwie instancje nie
   wzięły tego samego;
2. **uruchomienie osobnego procesu** przebiegu, w nowej sesji, żeby dało się
   zabić całą jego grupę razem z narzędziami bramek;
3. **anulowanie** — SIGTERM z okresem karencji, potem SIGKILL grupy procesów.
   Zabicie samego rodzica nie wystarcza: workery bramek robią `setsid()`;
4. **odtworzenie po restarcie** — zadanie po zmarłym nadzorcy jest
   `interrupted`, a nie wznawiane po cichu. Jeśli raport zdążył się zapisać,
   zadanie zostaje uczciwie domknięte jako `completed`.
"""

from __future__ import annotations

import errno
import fcntl
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
from typing import IO

from ..config import Settings
from ..storage import Database, Job, JobQueue, Repository

#: Dzierżawa jest krótka, a heartbeat częsty: im krócej, tym szybciej po
#: awarii wiadomo, że zadania nikt nie pilnuje.
LEASE_S = 30.0
HEARTBEAT_S = 5.0
POLL_S = 1.0

#: Ile czekamy, aż worker sam się zatrzyma po SIGTERM, zanim zabijemy grupę.
CANCEL_GRACE_S = 20.0


class SupervisorBusy(RuntimeError):
    """Inna instancja nadzorcy już pracuje na tym katalogu stanu."""


@dataclass
class ActiveRun:
    job_id: int
    process: subprocess.Popen[bytes]
    started_at: float
    terminate_sent_at: float | None = None


class Supervisor:
    def __init__(self, settings: Settings, poll_s: float = POLL_S) -> None:
        self.settings = settings
        self.poll_s = poll_s
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.database = Database(settings.db_path)
        self.queue = JobQueue(self.database)
        self.repository = Repository(self.database)
        self.active: ActiveRun | None = None
        self._lock: IO[str] | None = None
        self._last_heartbeat = 0.0

    # ------------------------------------------------------- pojedyncza instancja

    def acquire_lock(self) -> None:
        self.settings.ensure_state_dir()
        handle = self.settings.supervisor_lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SupervisorBusy(
                f"nadzorca już działa na katalogu {self.settings.state_dir}"
            ) from exc
        handle.write(f"{self.owner}\n")
        handle.flush()
        self._lock = handle

    def release_lock(self) -> None:
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

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
        # Argumenty jako lista, bez powłoki; własna sesja, żeby dało się zabić
        # całą grupę procesów razem z narzędziami bramek.
        process = subprocess.Popen(command, start_new_session=True)
        self.queue.set_worker_pid(job.id, process.pid)
        self.queue.append_event(job.id, "worker_started", message=f"proces {process.pid}")
        self.active = ActiveRun(job_id=job.id, process=process, started_at=time.monotonic())
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
        sig = signal.SIGKILL if escalate else signal.SIGTERM
        # Proces mógł się właśnie sam zakończyć — to nie jest błąd.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(active.process.pid), sig)
        if not escalate:
            active.terminate_sent_at = time.monotonic()
            self.queue.append_event(
                active.job_id, "cancelling", message="wysłano SIGTERM do procesu przebiegu"
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
            self._terminate(active, escalate=False)
            deadline = time.monotonic() + CANCEL_GRACE_S
            while active.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if active.process.poll() is None:  # pragma: no cover
                self._terminate(active, escalate=True)
                active.process.wait(timeout=5)
            self._finish(active)
        self.release_lock()


def supervisor_running(settings: Settings) -> bool:
    """Czy ktoś trzyma blokadę nadzorcy dla tego katalogu stanu."""
    path = settings.supervisor_lock_path
    if not path.exists():
        return False
    try:
        with path.open("r+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return True
                raise  # pragma: no cover
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:  # pragma: no cover
        return False
    return False


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - proces nadzorcy
    """Punkt wejścia procesu nadzorcy (`python -m gatekeeper_web.jobs.supervisor`)."""
    import argparse

    parser = argparse.ArgumentParser(description="Nadzorca kolejki panelu")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--poll", type=float, default=POLL_S)
    args = parser.parse_args(argv)

    settings = Settings(state_dir=Path(args.state_dir).expanduser())
    supervisor = Supervisor(settings, poll_s=args.poll)
    stopping = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        supervisor.run_forever(should_stop=lambda: stopping)
    except SupervisorBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
