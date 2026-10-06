"""Oceny znalezisk: „potwierdzony problem" albo „fałszywy alarm".

Bez ocen nie da się policzyć precyzji bramki, a bez precyzji nikt nie wie,
którą regułę skasować. Trzy rzeczy są tu świadome (PLAN-WEB-UI.md §6):

* ocena **nie zmienia** decyzji przebiegu ani nie tworzy wyjątku od reguły —
  to osobne działania, wykonywane świadomie;
* zmiana zdania **dopisuje wiersz**, a nie nadpisuje poprzedni: historia ocen
  jest częścią danych o skuteczności bramy;
* kontekstem jest **projekt**, nie sam fingerprint. Fingerprint w core liczy
  się z reguły, pliku i fragmentu kodu — nie zawiera repozytorium, więc ten
  sam ciąg w dwóch projektach to dwa różne problemy.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .db import Database

ReviewVerdict = Literal["true_positive", "false_positive"]

VERDICT_LABELS = {
    "true_positive": "potwierdzony problem",
    "false_positive": "fałszywy alarm",
}


@dataclass(frozen=True)
class Review:
    id: int
    project_id: int
    run_id: str
    fingerprint: str
    rule_id: str | None
    gate: str | None
    verdict: str
    author: str | None
    note: str | None
    created_at: str

    @property
    def label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict)


@dataclass(frozen=True)
class RulePrecision:
    rule_id: str
    findings: int
    judged: int
    true_positives: int

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.judged if self.judged else None


class ReviewStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        project_id: int,
        run_id: str,
        fingerprint: str,
        verdict: ReviewVerdict,
        rule_id: str | None = None,
        gate: str | None = None,
        author: str | None = None,
        note: str | None = None,
    ) -> Review:
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO reviews
                   (project_id, run_id, fingerprint, rule_id, gate, verdict, author, note,
                    created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (project_id, run_id, fingerprint, rule_id, gate, verdict, author, note, now),
            )
            review_id = int(cursor.lastrowid or 0)
            conn.execute(
                "INSERT INTO audit_events (at, kind, project_id, run_id, detail)"
                " VALUES (?,?,?,?,?)",
                (now, "finding.reviewed", project_id, run_id, f"{fingerprint}={verdict}"),
            )
            row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        return _review(row)

    def latest(self, project_id: int, fingerprint: str) -> Review | None:
        """Aktualna ocena: ostatni wiersz, nie „jakiś"."""
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE project_id = ? AND fingerprint = ?"
                " ORDER BY id DESC LIMIT 1",
                (project_id, fingerprint),
            ).fetchone()
        return _review(row) if row else None

    def latest_for_project(self, project_id: int) -> dict[str, Review]:
        """Po jednej, najnowszej ocenie na fingerprint — bez powielania wierszy."""
        with self.db.reading() as conn:
            rows = conn.execute(
                """SELECT r.* FROM reviews r
                   JOIN (SELECT fingerprint, MAX(id) AS id FROM reviews
                         WHERE project_id = ? GROUP BY fingerprint) last
                     ON last.id = r.id""",
                (project_id,),
            ).fetchall()
        return {str(row["fingerprint"]): _review(row) for row in rows}

    def history(self, project_id: int, fingerprint: str) -> list[Review]:
        with self.db.reading() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE project_id = ? AND fingerprint = ? ORDER BY id DESC",
                (project_id, fingerprint),
            ).fetchall()
        return [_review(row) for row in rows]

    def count(self, project_id: int | None = None) -> int:
        with self.db.reading() as conn:
            if project_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM reviews WHERE project_id = ?", (project_id,)
                ).fetchone()
        return int(row["n"])


def _review(row: sqlite3.Row | None) -> Review:
    assert row is not None
    return Review(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        run_id=str(row["run_id"]),
        fingerprint=str(row["fingerprint"]),
        rule_id=row["rule_id"],
        gate=row["gate"],
        verdict=str(row["verdict"]),
        author=row["author"],
        note=row["note"],
        created_at=str(row["created_at"]),
    )
