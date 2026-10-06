"""Połączenie i migracje bazy panelu.

Trzy decyzje, które trudno cofnąć po pierwszym wdrożeniu (PLAN-WEB-UI.md §5):

* **migracje numerowane**, nie `CREATE TABLE IF NOT EXISTS` w kółko — inaczej
  po pierwszej zmianie kolumny nie da się odróżnić bazy starej od nowej;
* **timeout na blokadę**, bo panel czyta w trakcie zapisu importu;
* treść raportu leży w bazie, nie w pliku obok — dzięki temu „zapisany indeks
  bez raportu” jest stanem niemożliwym, a nie stanem do posprzątania.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

#: Podnosić przy każdej migracji. Baza z wyższą wersją niż kod = odmowa startu.
SCHEMA_VERSION = 3

# Migracja to *lista instrukcji*, nie jeden skrypt: `executescript()` zamyka
# otwartą transakcję, więc DDL puszczony skryptem nie byłby atomowy.
_MIGRATION_1 = (
    """
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    repo_label  TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

-- Kompletny raport + zdenormalizowany indeks do filtrowania. Klucz główny to
-- `(project_id, run_id)`, nie sam `run_id`: fingerprinty i identyfikatory
-- przebiegów z core nie zawierają repozytorium (PLAN-WEB-UI.md §5).
CREATE TABLE run_reports (
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id           TEXT    NOT NULL,
    format_version   TEXT    NOT NULL,
    origin           TEXT    NOT NULL CHECK (origin IN ('imported', 'engine')),
    content_hash     TEXT    NOT NULL,
    payload          TEXT    NOT NULL,
    verdict          TEXT    NOT NULL,
    started_at       TEXT    NOT NULL,
    duration_s       REAL,
    base_sha         TEXT    NOT NULL,
    head_sha         TEXT    NOT NULL,
    repo             TEXT,
    policy_version   INTEGER,
    gate_count       INTEGER NOT NULL,
    gate_error_count INTEGER NOT NULL,
    finding_count    INTEGER NOT NULL,
    stored_at        TEXT    NOT NULL,
    source_label     TEXT,
    PRIMARY KEY (project_id, run_id)
);

CREATE INDEX idx_reports_started ON run_reports(started_at DESC);
CREATE INDEX idx_reports_verdict ON run_reports(project_id, verdict);

-- Ślad działań operatora. Etap 1 zapisuje rejestrację projektu i import;
-- uruchomienie i anulowanie dopisze etap 3/4 do tej samej tabeli.
CREATE TABLE audit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    project_id INTEGER,
    run_id     TEXT,
    detail     TEXT
);

CREATE INDEX idx_audit_at ON audit_events(at DESC);
    """,
)

# Etapy 2–5: rejestr repozytoriów, profile polityki, kolejka zadań, oceny.
# Osobna migracja, nie poprawka w `_MIGRATION_1`: baza z etapu 1 istnieje
# na dysku operatora i ma się zmigrować, a nie zostać odtworzona od zera.
_MIGRATION_2 = (
    """
-- Ścieżka lokalnego repozytorium i domyślny profil polityki. Kolumny są
-- dodawane, a nie tworzone od nowa: projekty z etapu 1 zostają.
ALTER TABLE projects ADD COLUMN repo_path TEXT;
ALTER TABLE projects ADD COLUMN policy_profile_id INTEGER;

CREATE TABLE policy_profiles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    slug               TEXT    NOT NULL UNIQUE,
    name               TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    active_revision_id INTEGER
);

-- Wersja polityki jest niezmienna po aktywacji. Historyczny przebieg musi dać
-- się odtworzyć razem z polityką, według której zapadła jego decyzja.
CREATE TABLE policy_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES policy_profiles(id) ON DELETE CASCADE,
    revision        INTEGER NOT NULL,
    state           TEXT    NOT NULL CHECK (state IN ('draft', 'active', 'retired')),
    policy_yaml     TEXT    NOT NULL,
    exceptions_yaml TEXT,
    scope_map_yaml  TEXT,
    content_hash    TEXT    NOT NULL,
    author          TEXT,
    note            TEXT,
    created_at      TEXT    NOT NULL,
    activated_at    TEXT,
    UNIQUE (profile_id, revision)
);

-- `input_json` to zamrożone wejście przebiegu: SHA, treść polityki, wybrane
-- bramki, wersje narzędzi. Zadanie wykonuje dokładnie to, nawet jeśli w
-- międzyczasie przesunie się gałąź albo operator aktywuje inną politykę.
CREATE TABLE jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    state            TEXT    NOT NULL CHECK (state IN (
                         'queued', 'preparing', 'running', 'cancelling',
                         'completed', 'cancelled', 'interrupted', 'failed')),
    input_json       TEXT    NOT NULL,
    input_hash       TEXT    NOT NULL,
    idempotency_key  TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    lease_owner      TEXT,
    lease_expires_at TEXT,
    worker_pid       INTEGER,
    run_id           TEXT,
    error            TEXT,
    retry_of         INTEGER,
    created_at       TEXT    NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);

-- Podwójne kliknięcie „Uruchom" ma dać jedno zadanie, a jawne ponowienie
-- — nowe (PLAN-WEB-UI.md §9, scenariusz 6).
CREATE UNIQUE INDEX idx_jobs_idempotency ON jobs(project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_jobs_state ON jobs(state, id);

CREATE TABLE job_events (
    job_id    INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    at        TEXT    NOT NULL,
    kind      TEXT    NOT NULL,
    gate      TEXT,
    message   TEXT,
    completed INTEGER,
    total     INTEGER,
    PRIMARY KEY (job_id, seq)
);

-- Ocena znaleziska. Kontekstem jest projekt, nie sam fingerprint: ten sam
-- ciąg znaków w dwóch repozytoriach to dwa różne problemy (plan §5).
CREATE TABLE reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id      TEXT    NOT NULL,
    fingerprint TEXT    NOT NULL,
    rule_id     TEXT,
    gate        TEXT,
    verdict     TEXT    NOT NULL CHECK (verdict IN ('true_positive', 'false_positive')),
    author      TEXT,
    note        TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_reviews_lookup ON reviews(project_id, fingerprint, id DESC);

ALTER TABLE run_reports ADD COLUMN caused_incident INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_reports ADD COLUMN incident_note TEXT;
ALTER TABLE run_reports ADD COLUMN job_id INTEGER;
    """,
)

# Indeks znalezisk. Metryki muszą umieć powiedzieć, czy liczą *wystąpienia*,
# czy *unikalne problemy* — z raportu trzymanego jako JSON nie da się tego
# policzyć bez czytania wszystkiego (PLAN-WEB-UI.md §6).
_MIGRATION_3 = (
    """
CREATE TABLE report_findings (
    project_id  INTEGER NOT NULL,
    run_id      TEXT    NOT NULL,
    fingerprint TEXT    NOT NULL,
    gate        TEXT    NOT NULL,
    rule_id     TEXT    NOT NULL,
    severity    TEXT    NOT NULL,
    PRIMARY KEY (project_id, run_id, fingerprint)
);

CREATE INDEX idx_report_findings_rule ON report_findings(project_id, rule_id);
CREATE INDEX idx_report_findings_fp ON report_findings(project_id, fingerprint);
    """,
)


def _backfill_findings(conn: sqlite3.Connection) -> None:
    """Uzupełnia indeks z raportów zapisanych przed tą migracją.

    Migracja czysto SQL-owa nie umie zajrzeć do JSON-a, a panel z etapu 1 ma
    już raporty na dysku — bez tego kroku ich znaleziska zniknęłyby z metryk.
    """
    rows = conn.execute("SELECT project_id, run_id, payload FROM run_reports").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):  # pragma: no cover - raport nie do odczytu
            continue
        for gate in payload.get("gates") or []:
            for finding in gate.get("findings") or []:
                conn.execute(
                    "INSERT OR REPLACE INTO report_findings"
                    " (project_id, run_id, fingerprint, gate, rule_id, severity)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        row["project_id"],
                        row["run_id"],
                        str(finding.get("fingerprint") or ""),
                        str(gate.get("gate") or ""),
                        str(finding.get("rule_id") or ""),
                        str(finding.get("severity") or ""),
                    ),
                )


#: Migracja = numer, instrukcje DDL i opcjonalny krok w Pythonie (przeniesienie
#: danych, którego nie da się wyrazić w samym SQL-u).
MIGRATIONS: tuple[tuple[int, tuple[str, ...], Callable[[sqlite3.Connection], None] | None], ...] = (
    (1, _MIGRATION_1, None),
    (2, _MIGRATION_2, None),
    (3, _MIGRATION_3, _backfill_findings),
)


def _split(statements: tuple[str, ...]) -> list[str]:
    """Rozbija bloki DDL na pojedyncze instrukcje."""
    out: list[str] = []
    for block in statements:
        out.extend(part.strip() for part in block.split(";") if part.strip())
    return out


class SchemaTooNew(RuntimeError):
    """Baza zapisana nowszym panelem — migracja w dół nie istnieje."""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        # WAL: czytanie historii w trakcie importu nie ma prawa zwrócić błędu.
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Krótka transakcja: albo cały import, albo nic."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            current = int(row["v"] or 0)
            if current > SCHEMA_VERSION:
                raise SchemaTooNew(
                    f"baza {self.path} ma schemat w wersji {current}, a ten panel zna "
                    f"{SCHEMA_VERSION} — uruchom nowszą wersję pakietu zamiast migrować w dół"
                )
            for version, statements, step in MIGRATIONS:
                if version <= current:
                    continue
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _split(statements):
                        conn.execute(statement)
                    if step is not None:
                        step(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(UTC).isoformat()),
                    )
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        finally:
            conn.close()
