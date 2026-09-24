"""Odczyt i zapis stanu panelu. Jedyne miejsce, które zna SQL."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .db import Database

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: Filtr historii nie przyjmuje nazwy kolumny z zapytania HTTP — dozwolone
#: porządki są wyliczone tutaj, a nie sklejane ze stringa użytkownika.
_ORDERS = {
    "started_desc": "started_at DESC, run_id DESC",
    "started_asc": "started_at ASC, run_id ASC",
    "findings_desc": "finding_count DESC, started_at DESC",
}


class ReportConflict(Exception):
    """Ten sam `(projekt, run_id)` z inną treścią raportu."""


@dataclass(frozen=True)
class Project:
    id: int
    slug: str
    name: str
    repo_label: str | None
    archived: bool
    created_at: str
    #: Kanoniczna ścieżka lokalnego repozytorium. `None` = projekt służy tylko
    #: do przeglądania zaimportowanych raportów i nie da się z niego uruchomić
    #: kontroli.
    repo_path: str | None = None
    policy_profile_id: int | None = None

    @property
    def runnable(self) -> bool:
        return bool(self.repo_path) and self.policy_profile_id is not None


@dataclass(frozen=True)
class ReportRow:
    """Wiersz indeksu + kompletny raport w polu `payload`."""

    project_id: int
    project_slug: str
    project_name: str
    run_id: str
    format_version: str
    origin: str
    content_hash: str
    verdict: str
    started_at: str
    duration_s: float | None
    base_sha: str
    head_sha: str
    repo: str | None
    policy_version: int | None
    gate_count: int
    gate_error_count: int
    finding_count: int
    stored_at: str
    source_label: str | None
    caused_incident: bool = False
    incident_note: str | None = None
    job_id: int | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunFilter:
    project_id: int | None = None
    verdict: str | None = None
    since: str | None = None
    until: str | None = None
    query: str | None = None
    order: str = "started_desc"
    limit: int = 25
    offset: int = 0


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "projekt"


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ projekty

    def create_project(self, name: str, repo_label: str | None = None) -> Project:
        base = slugify(name)
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            slug = base
            suffix = 2
            while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
                slug = f"{base}-{suffix}"
                suffix += 1
            cur = conn.execute(
                "INSERT INTO projects (slug, name, repo_label, archived, created_at)"
                " VALUES (?,?,?,0,?)",
                (slug, name.strip(), repo_label, now),
            )
            project_id = int(cur.lastrowid or 0)
            _audit(conn, "project.created", project_id, None, f"slug={slug}")
        return Project(project_id, slug, name.strip(), repo_label, False, now)

    def update_project(
        self,
        project_id: int,
        *,
        name: str | None = None,
        repo_path: str | None = None,
        policy_profile_id: int | None = None,
        archived: bool | None = None,
    ) -> Project:
        """Zmiana metadanych projektu. Każda jest zapisywana w śladzie działań."""
        updates: list[str] = []
        params: list[Any] = []
        detail: list[str] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
            detail.append("nazwa")
        if repo_path is not None:
            updates.append("repo_path = ?")
            params.append(repo_path)
            detail.append(f"repozytorium={repo_path}")
        if policy_profile_id is not None:
            updates.append("policy_profile_id = ?")
            params.append(policy_profile_id)
            detail.append(f"profil={policy_profile_id}")
        if archived is not None:
            updates.append("archived = ?")
            params.append(int(archived))
            detail.append("zarchiwizowany" if archived else "przywrócony")
        if updates:
            with self.db.transaction() as conn:
                conn.execute(
                    f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
                    (*params, project_id),
                )
                _audit(conn, "project.updated", project_id, None, ", ".join(detail))
        project = self.get_project(project_id)
        assert project is not None
        return project

    def mark_incident(self, project_id: int, run_id: str, note: str | None = None) -> None:
        """Oznacza przebieg, po którym zmiana wywołała incydent na produkcji.

        Nie zmienia raportu ani decyzji — dopisuje fakt, który zdarzył się
        później (PLAN-WEB-UI.md §6).
        """
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE run_reports SET caused_incident = 1, incident_note = ?"
                " WHERE project_id = ? AND run_id = ?",
                (note, project_id, run_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"projekt {project_id} nie ma przebiegu {run_id!r}")
            _audit(conn, "run.incident", project_id, run_id, note)

    def list_projects(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY name COLLATE NOCASE"
        with self.db.reading() as conn:
            return [_project(row) for row in conn.execute(sql)]

    def get_project(self, project_id: int) -> Project | None:
        with self.db.reading() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project(row) if row else None

    def get_project_by_slug(self, slug: str) -> Project | None:
        with self.db.reading() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return _project(row) if row else None

    def set_archived(self, project_id: int, archived: bool) -> None:
        """Archiwizacja ukrywa projekt w listach; nie kasuje raportów ani plików."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE projects SET archived = ? WHERE id = ?", (int(archived), project_id)
            )
            _audit(conn, "project.archived" if archived else "project.restored", project_id)

    # ------------------------------------------------------------- raporty

    def store_report(
        self,
        project_id: int,
        payload: dict[str, Any],
        index: dict[str, Any],
        content_hash: str,
        format_version: str,
        origin: str = "imported",
        source_label: str | None = None,
        job_id: int | None = None,
    ) -> tuple[ReportRow, bool]:
        """Zapisuje raport. Zwraca `(wiersz, czy_nowy)`.

        Powtórny import tej samej treści jest bezgłośnym „już mam” — to samo
        `(projekt, run_id)` z inną treścią to `ReportConflict`. Bez tego
        podwójne kliknięcie „Importuj” cicho nadpisuje historię.
        """
        run_id = str(payload["run_id"])
        now = datetime.now(UTC).isoformat()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT content_hash FROM run_reports WHERE project_id = ? AND run_id = ?",
                (project_id, run_id),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != content_hash:
                    raise ReportConflict(
                        f"przebieg {run_id} jest już zapisany w tym projekcie z inną treścią "
                        "— to nie jest ten sam raport"
                    )
                created = False
            else:
                conn.execute(
                    """INSERT INTO run_reports
                       (project_id, run_id, format_version, origin, content_hash, payload,
                        verdict, started_at, duration_s, base_sha, head_sha, repo,
                        policy_version, gate_count, gate_error_count, finding_count,
                        stored_at, source_label, job_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        project_id,
                        run_id,
                        format_version,
                        origin,
                        content_hash,
                        serialized,
                        index["verdict"],
                        index["started_at"],
                        index["duration_s"],
                        index["base_sha"],
                        index["head_sha"],
                        index["repo"],
                        index["policy_version"],
                        index["gate_count"],
                        index["gate_error_count"],
                        index["finding_count"],
                        now,
                        source_label,
                        job_id,
                    ),
                )
                _index_findings(conn, project_id, run_id, payload)
                _audit(conn, f"report.{origin}", project_id, run_id, source_label)
                created = True
            row = _report_row(_fetch_report(conn, project_id, run_id), with_payload=True)
        return row, created

    def report_for_job(self, job_id: int) -> ReportRow | None:
        """Raport zapisany przez zadanie — po nim poznajemy, że praca się udała
        mimo zgonu procesu tuż przed zmianą stanu."""
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT r.*, p.slug AS project_slug, p.name AS project_name"
                " FROM run_reports r JOIN projects p ON p.id = r.project_id"
                " WHERE r.job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
        return _report_row(row, with_payload=False) if row else None

    def get_report(self, project_id: int, run_id: str) -> ReportRow | None:
        with self.db.reading() as conn:
            row = _fetch_report(conn, project_id, run_id)
        return _report_row(row, with_payload=True) if row else None

    def list_reports(self, flt: RunFilter) -> list[ReportRow]:
        sql, params = _filter_sql(flt)
        order = _ORDERS.get(flt.order, _ORDERS["started_desc"])
        sql = (
            "SELECT r.*, p.slug AS project_slug, p.name AS project_name"
            " FROM run_reports r JOIN projects p ON p.id = r.project_id"
            f" {sql} ORDER BY {order} LIMIT ? OFFSET ?"
        )
        with self.db.reading() as conn:
            rows = conn.execute(sql, (*params, max(1, flt.limit), max(0, flt.offset))).fetchall()
        return [_report_row(row, with_payload=False) for row in rows]

    def count_reports(self, flt: RunFilter) -> int:
        sql, params = _filter_sql(flt)
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM run_reports r"
                " JOIN projects p ON p.id = r.project_id " + sql,
                params,
            ).fetchone()
        return int(row["n"])

    def verdict_counts(self, project_id: int | None = None) -> dict[str, int]:
        sql = "SELECT verdict, COUNT(*) AS n FROM run_reports"
        params: tuple[Any, ...] = ()
        if project_id is not None:
            sql += " WHERE project_id = ?"
            params = (project_id,)
        sql += " GROUP BY verdict"
        with self.db.reading() as conn:
            return {str(row["verdict"]): int(row["n"]) for row in conn.execute(sql, params)}

    def audit_tail(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.reading() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------- pomocnicze


def _filter_sql(flt: RunFilter) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if flt.project_id is not None:
        clauses.append("r.project_id = ?")
        params.append(flt.project_id)
    if flt.verdict:
        clauses.append("r.verdict = ?")
        params.append(flt.verdict)
    if flt.since:
        clauses.append("r.started_at >= ?")
        params.append(flt.since)
    if flt.until:
        clauses.append("r.started_at <= ?")
        params.append(flt.until)
    if flt.query:
        # LIKE z parametrem, nie sklejanie stringów: pole tekstowe z formularza
        # nie ma prawa dopisać niczego do zapytania.
        needle = f"%{flt.query.strip()}%"
        clauses.append(
            "(r.run_id LIKE ? OR r.base_sha LIKE ? OR r.head_sha LIKE ?"
            " OR r.repo LIKE ? OR p.name LIKE ?)"
        )
        params.extend([needle] * 5)
    return ("WHERE " + " AND ".join(clauses) if clauses else "", tuple(params))


def _fetch_report(conn: sqlite3.Connection, project_id: int, run_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT r.*, p.slug AS project_slug, p.name AS project_name"
        " FROM run_reports r JOIN projects p ON p.id = r.project_id"
        " WHERE r.project_id = ? AND r.run_id = ?",
        (project_id, run_id),
    ).fetchone()
    return row


def _project(row: sqlite3.Row) -> Project:
    return Project(
        id=int(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        repo_label=row["repo_label"],
        archived=bool(row["archived"]),
        created_at=str(row["created_at"]),
        repo_path=row["repo_path"],
        policy_profile_id=row["policy_profile_id"],
    )


def _report_row(row: sqlite3.Row | None, with_payload: bool) -> ReportRow:
    assert row is not None  # wywoływane wyłącznie po sprawdzeniu istnienia
    return ReportRow(
        project_id=int(row["project_id"]),
        project_slug=str(row["project_slug"]),
        project_name=str(row["project_name"]),
        run_id=str(row["run_id"]),
        format_version=str(row["format_version"]),
        origin=str(row["origin"]),
        content_hash=str(row["content_hash"]),
        verdict=str(row["verdict"]),
        started_at=str(row["started_at"]),
        duration_s=row["duration_s"],
        base_sha=str(row["base_sha"]),
        head_sha=str(row["head_sha"]),
        repo=row["repo"],
        policy_version=row["policy_version"],
        gate_count=int(row["gate_count"]),
        gate_error_count=int(row["gate_error_count"]),
        finding_count=int(row["finding_count"]),
        stored_at=str(row["stored_at"]),
        source_label=row["source_label"],
        caused_incident=bool(row["caused_incident"]),
        incident_note=row["incident_note"],
        job_id=row["job_id"],
        payload=json.loads(row["payload"]) if with_payload else None,
    )


def _index_findings(
    conn: sqlite3.Connection, project_id: int, run_id: str, payload: dict[str, Any]
) -> None:
    """Płaski indeks znalezisk obok raportu — z niego liczą się metryki."""
    conn.executemany(
        "INSERT OR REPLACE INTO report_findings"
        " (project_id, run_id, fingerprint, gate, rule_id, severity) VALUES (?,?,?,?,?,?)",
        [
            (
                project_id,
                run_id,
                str(finding.get("fingerprint") or ""),
                str(gate.get("gate") or ""),
                str(finding.get("rule_id") or ""),
                str(finding.get("severity") or ""),
            )
            for gate in payload.get("gates") or []
            for finding in gate.get("findings") or []
        ],
    )


def _audit(
    conn: sqlite3.Connection,
    kind: str,
    project_id: int | None = None,
    run_id: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_events (at, kind, project_id, run_id, detail) VALUES (?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), kind, project_id, run_id, detail),
    )
