"""Kolejka zadań: stany, dzierżawa nadzorcy, zdarzenia postępu.

Stan zadania to co innego niż wynik bramki i co innego niż decyzja polityki
(PLAN-WEB-UI.md §3). Ta tabela odpowiada wyłącznie na pytanie „czy udało się
wykonać pracę".

Trzy reguły, bez których kolejka kłamie:

* **każda zmiana stanu jest jednym atomowym UPDATE-em z warunkiem na stan
  poprzedni** — wyścig „zakończone kontra anulowane" rozstrzyga baza, nie
  kolejność `if`-ów w Pythonie;
* **dzierżawa wygasa** — nadzorca, który zniknął razem z prądem, nie blokuje
  zadania na zawsze; po restarcie jego zadania są `interrupted`, a nie
  wskrzeszane po cichu;
* **zdarzenia mają numer kolejny w obrębie zadania**, więc przeglądarka pyta
  „co nowego po N" zamiast pobierać całą historię co dwie sekundy.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database

#: Stany nieterminalne, w których zadanie zajmuje nadzorcę.
ACTIVE_STATES = ("queued", "preparing", "running", "cancelling")
TERMINAL_STATES = ("completed", "cancelled", "interrupted", "failed")

STATE_LABELS = {
    "queued": "w kolejce",
    "preparing": "przygotowanie",
    "running": "trwa",
    "cancelling": "zatrzymywanie",
    "completed": "zakończone",
    "cancelled": "anulowane",
    "interrupted": "przerwane",
    "failed": "awaria",
}


class JobError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    id: int
    project_id: int
    state: str
    input: dict[str, Any]
    input_hash: str
    idempotency_key: str | None
    cancel_requested: bool
    lease_owner: str | None
    lease_expires_at: str | None
    worker_pid: int | None
    run_id: str | None
    error: str | None
    retry_of: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def has_result(self) -> bool:
        """Decyzja polityki istnieje wyłącznie dla zadania z zapisanym raportem."""
        return self.state == "completed" and bool(self.run_id)


@dataclass(frozen=True)
class JobEvent:
    seq: int
    at: str
    kind: str
    gate: str | None
    message: str
    completed: int | None
    total: int | None


def input_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class JobQueue:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ zlecenie

    def enqueue(
        self,
        project_id: int,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        retry_of: int | None = None,
    ) -> tuple[Job, bool]:
        """Dodaje zadanie. Zwraca `(zadanie, czy_nowe)`.

        Ten sam klucz idempotencji zwraca istniejące zadanie — podwójne
        kliknięcie „Uruchom" nie ma prawa zlecić dwóch analiz.
        """
        digest = input_hash(payload)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE project_id = ? AND idempotency_key = ?",
                    (project_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return _job(existing), False
            cursor = conn.execute(
                """INSERT INTO jobs
                   (project_id, state, input_json, input_hash, idempotency_key,
                    retry_of, created_at)
                   VALUES (?, 'queued', ?, ?, ?, ?, ?)""",
                (project_id, serialized, digest, idempotency_key, retry_of, now),
            )
            job_id = int(cursor.lastrowid or 0)
            _event(conn, job_id, "queued", message="zadanie przyjęte do kolejki")
            conn.execute(
                "INSERT INTO audit_events (at, kind, project_id, run_id, detail)"
                " VALUES (?,?,?,?,?)",
                (now, "job.queued", project_id, None, f"zadanie {job_id}"),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row), True

    # -------------------------------------------------------------- odczyt

    def get(self, job_id: int) -> Job | None:
        with self.db.reading() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(
        self,
        project_id: int | None = None,
        states: tuple[str, ...] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if states:
            clauses.append(f"state IN ({','.join('?' * len(states))})")
            params.extend(states)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.reading() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, max(1, limit), max(0, offset)),
            ).fetchall()
        return [_job(row) for row in rows]

    def count_jobs(self, project_id: int | None = None) -> int:
        with self.db.reading() as conn:
            if project_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM jobs WHERE project_id = ?", (project_id,)
                ).fetchone()
        return int(row["n"])

    def is_cancel_requested(self, job_id: int) -> bool:
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    # ------------------------------------------------------------- nadzorca

    def claim(self, owner: str, lease_s: float = 30.0) -> Job | None:
        """Zabiera najstarsze zadanie z kolejki. MVP: jedno naraz.

        Wybór i zmiana stanu dzieją się w jednej transakcji `BEGIN IMMEDIATE`,
        więc dwóch nadzorców nie weźmie tego samego zadania.
        """
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            busy = conn.execute(
                f"SELECT 1 FROM jobs WHERE state IN ({','.join('?' * 3)}) LIMIT 1",
                ("preparing", "running", "cancelling"),
            ).fetchone()
            if busy is not None:
                return None
            row = conn.execute(
                "SELECT * FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["id"])
            conn.execute(
                "UPDATE jobs SET state = 'preparing', lease_owner = ?, lease_expires_at = ?,"
                " started_at = COALESCE(started_at, ?) WHERE id = ? AND state = 'queued'",
                (owner, (now + timedelta(seconds=lease_s)).isoformat(), now.isoformat(), job_id),
            )
            _event(conn, job_id, "preparing", message="nadzorca przejął zadanie")
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(claimed)

    def heartbeat(self, job_id: int, owner: str, lease_s: float = 30.0) -> bool:
        expires = (datetime.now(UTC) + timedelta(seconds=lease_s)).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET lease_expires_at = ? WHERE id = ? AND lease_owner = ?",
                (expires, job_id, owner),
            )
            return cursor.rowcount > 0

    def set_worker_pid(self, job_id: int, pid: int | None) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE jobs SET worker_pid = ? WHERE id = ?", (pid, job_id))

    def transition(
        self,
        job_id: int,
        new_state: str,
        expected: tuple[str, ...],
        *,
        run_id: str | None = None,
        error: str | None = None,
        message: str = "",
    ) -> bool:
        """Jedna atomowa zmiana stanu. `False` = ktoś nas ubiegł."""
        if new_state not in ACTIVE_STATES + TERMINAL_STATES:
            raise JobError(f"nieznany stan zadania: {new_state!r}")
        now = datetime.now(UTC).isoformat()
        finished = now if new_state in TERMINAL_STATES else None
        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"""UPDATE jobs SET state = ?, run_id = COALESCE(?, run_id),
                        error = COALESCE(?, error), finished_at = ?,
                        lease_owner = CASE WHEN ? IS NULL THEN lease_owner ELSE NULL END
                    WHERE id = ? AND state IN ({','.join('?' * len(expected))})""",
                (new_state, run_id, error, finished, finished, job_id, *expected),
            )
            if cursor.rowcount == 0:
                return False
            _event(conn, job_id, new_state, message=message or STATE_LABELS.get(new_state, ""))
            if new_state in TERMINAL_STATES:
                conn.execute(
                    "INSERT INTO audit_events (at, kind, project_id, run_id, detail)"
                    " SELECT ?, ?, project_id, ?, ? FROM jobs WHERE id = ?",
                    (now, f"job.{new_state}", run_id, f"zadanie {job_id}", job_id),
                )
        return True

    def request_cancel(self, job_id: int) -> Job:
        """Idempotentne żądanie zatrzymania.

        Zadanie w kolejce kończy się od razu — nie ma czego zatrzymywać.
        Zadanie w toku dostaje flagę; sprząta je nadzorca razem z procesami
        bramek, bo to on wie, co faktycznie działa.
        """
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobError(f"nie znam zadania {job_id}")
            state = str(row["state"])
            conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            if state == "queued":
                conn.execute(
                    "UPDATE jobs SET state = 'cancelled', finished_at = ?"
                    " WHERE id = ? AND state = 'queued'",
                    (now, job_id),
                )
                _event(conn, job_id, "cancelled", message="anulowane przed uruchomieniem")
            elif state in ("preparing", "running"):
                conn.execute(
                    "UPDATE jobs SET state = 'cancelling' WHERE id = ? AND state IN"
                    " ('preparing', 'running')",
                    (job_id,),
                )
                _event(conn, job_id, "cancelling", message="żądanie zatrzymania przekazane")
            conn.execute(
                "INSERT INTO audit_events (at, kind, project_id, run_id, detail)"
                " VALUES (?,?,?,?,?)",
                (now, "job.cancel_requested", int(row["project_id"]), None, f"zadanie {job_id}"),
            )
            refreshed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(refreshed)

    def reclaim_expired(self) -> list[int]:
        """Zadania po zmarłym nadzorcy. Przerwane, nie wznowione po cichu."""
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            rows = conn.execute(
                f"""SELECT id FROM jobs WHERE state IN ({','.join('?' * 3)})
                    AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",
                ("preparing", "running", "cancelling", now),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            for job_id in ids:
                conn.execute(
                    "UPDATE jobs SET state = 'interrupted', finished_at = ?, lease_owner = NULL,"
                    " error = COALESCE(error, ?) WHERE id = ?",
                    (
                        now,
                        "nadzorca zniknął w trakcie przebiegu — wynik nie powstał",
                        job_id,
                    ),
                )
                _event(
                    conn,
                    job_id,
                    "interrupted",
                    message="zadanie przerwane: nadzorca przestał odpowiadać",
                )
        return ids

    # ----------------------------------------------------------- zdarzenia

    def append_event(
        self,
        job_id: int,
        kind: str,
        message: str = "",
        gate: str | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> int:
        with self.db.transaction() as conn:
            return _event(conn, job_id, kind, message, gate, completed, total)

    def events(self, job_id: int, after: int = 0, limit: int = 200) -> list[JobEvent]:
        with self.db.reading() as conn:
            rows = conn.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (job_id, after, max(1, limit)),
            ).fetchall()
        return [
            JobEvent(
                seq=int(row["seq"]),
                at=str(row["at"]),
                kind=str(row["kind"]),
                gate=row["gate"],
                message=str(row["message"] or ""),
                completed=row["completed"],
                total=row["total"],
            )
            for row in rows
        ]


# --------------------------------------------------------------- pomocnicze


def _event(
    conn: sqlite3.Connection,
    job_id: int,
    kind: str,
    message: str = "",
    gate: str | None = None,
    completed: int | None = None,
    total: int | None = None,
) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS n FROM job_events WHERE job_id = ?", (job_id,)
    ).fetchone()
    seq = int(row["n"]) + 1
    conn.execute(
        "INSERT INTO job_events (job_id, seq, at, kind, gate, message, completed, total)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (job_id, seq, datetime.now(UTC).isoformat(), kind, gate, message, completed, total),
    )
    return seq


def _job(row: sqlite3.Row | None) -> Job:
    assert row is not None
    return Job(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        state=str(row["state"]),
        input=json.loads(row["input_json"]),
        input_hash=str(row["input_hash"]),
        idempotency_key=row["idempotency_key"],
        cancel_requested=bool(row["cancel_requested"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        worker_pid=row["worker_pid"],
        run_id=row["run_id"],
        error=row["error"],
        retry_of=row["retry_of"],
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
