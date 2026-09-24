"""Metryki skuteczności bramy liczone z bazy panelu.

Dwa błędy, których ten moduł unika (PLAN-WEB-UI.md §6):

* **powielanie wierszy** — złączenie znalezisk z *całą* historią ocen liczy
  jedno znalezisko tyle razy, ile razy ktoś zmienił zdanie. Tutaj liczy się
  wyłącznie **ostatnia** ocena dla pary (projekt, fingerprint);
* **mylenie wystąpień z problemami** — to samo znalezisko w dziesięciu
  przebiegach to jeden problem i dziesięć wystąpień. Obie liczby są podane
  osobno i nazwane.

Metryka bez danych jest oznaczona jako brak danych. Zero i „nikt tego nie
zmierzył" znaczą co innego.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from ..storage import Database


@dataclass(frozen=True)
class Metric:
    name: str
    value: float | None
    unit: str = ""
    target: str = ""
    note: str = ""

    @property
    def display(self) -> str:
        if self.value is None:
            return "brak danych"
        if self.unit == "%":
            return f"{self.value * 100:.1f}".replace(".", ",") + "%"
        if self.unit == "s":
            return f"{self.value:.1f}".replace(".", ",") + " s"
        return f"{self.value:g}"


@dataclass(frozen=True)
class RuleStats:
    rule_id: str
    occurrences: int
    unique_findings: int
    judged: int
    true_positives: int

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.judged if self.judged else None

    @property
    def precision_display(self) -> str:
        if self.precision is None:
            return "brak ocen"
        return f"{self.precision * 100:.0f}%"


@dataclass
class MetricsReport:
    days: int
    project_id: int | None
    runs: int
    metrics: list[Metric] = field(default_factory=list)
    rules: list[RuleStats] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def collect(db: Database, days: int = 30, project_id: int | None = None) -> MetricsReport:
    where = "started_at >= datetime('now', ?)"
    params: list[Any] = [f"-{days} days"]
    if project_id is not None:
        where += " AND project_id = ?"
        params.append(project_id)

    with db.reading() as conn:
        rows = conn.execute(
            f"SELECT verdict, duration_s, caused_incident FROM run_reports WHERE {where}",
            params,
        ).fetchall()
        rules = _rule_stats(conn, project_id, days)

    total = len(rows)
    report = MetricsReport(days=days, project_id=project_id, runs=total, rules=rules)
    if total == 0:
        report.metrics.append(
            Metric("Przebiegi w okresie", None, note="brak zapisanych przebiegów")
        )
        return report

    blocked = sum(1 for r in rows if r["verdict"] == "BLOCK")
    review = sum(1 for r in rows if r["verdict"] == "PASS-WITH-REVIEW")
    clean = sum(1 for r in rows if r["verdict"] == "PASS")

    report.metrics.append(Metric("Zablokowane", blocked / total, "%"))
    report.metrics.append(Metric("Skierowane do człowieka", review / total, "%"))
    report.metrics.append(
        Metric("Bez ręcznego review", clean / total, "%", target="rosnący, ale nie kosztem escapów")
    )

    durations = [r["duration_s"] for r in rows if r["duration_s"] is not None]
    report.metrics.append(
        Metric("Mediana czasu przebiegu", statistics.median(durations), "s", target="< 1200 s")
        if durations
        else Metric("Mediana czasu przebiegu", None, note="żaden przebieg nie ma pomiaru czasu")
    )

    judged = sum(r.judged for r in rules)
    true_positives = sum(r.true_positives for r in rules)
    report.metrics.append(
        Metric("Precyzja bramki", true_positives / judged, "%", target="> 80%")
        if judged
        else Metric(
            "Precyzja bramki",
            None,
            note="nikt nie ocenił jeszcze żadnego znaleziska — oceny są na stronie znaleziska",
        )
    )

    incidents = sum(1 for r in rows if r["caused_incident"])
    passed_through = total - blocked
    if incidents == 0:
        # Zero incydentów i brak oznaczania incydentów wyglądają w bazie tak
        # samo, a znaczą co innego.
        report.metrics.append(
            Metric(
                "Escape rate",
                None,
                note="żaden przebieg nie jest oznaczony jako incydent",
            )
        )
    elif passed_through:
        report.metrics.append(
            Metric("Escape rate", incidents / passed_through, "%", target="trend malejący")
        )
    else:
        # Są incydenty, ale nic nie przeszło bramy — mianownik jest zerem.
        # Pominięcie metryki wyglądałoby jak brak incydentów.
        report.metrics.append(
            Metric(
                "Escape rate",
                None,
                note=f"{incidents} incydentów, ale żaden przebieg nie przeszedł bramy "
                "— nie ma czego dzielić",
            )
        )

    report.notes.append(
        "Wystąpienia liczą znalezisko w każdym przebiegu osobno; problemy liczą "
        "unikalne fingerprinty. Precyzja bierze pod uwagę wyłącznie ostatnią ocenę "
        "dla pary projekt + fingerprint."
    )
    return report


def _rule_stats(conn: Any, project_id: int | None, days: int) -> list[RuleStats]:
    params: list[Any] = [f"-{days} days"]
    project_clause = ""
    if project_id is not None:
        project_clause = " AND f.project_id = ?"
        params.append(project_id)

    # `last` wybiera po jednej, najnowszej ocenie na (projekt, fingerprint) —
    # dzięki temu zmiana zdania nie mnoży wierszy w statystyce.
    rows = conn.execute(
        f"""
        WITH last AS (
            SELECT r.project_id, r.fingerprint, r.verdict
            FROM reviews r
            JOIN (SELECT project_id, fingerprint, MAX(id) AS id FROM reviews
                  GROUP BY project_id, fingerprint) newest ON newest.id = r.id
        )
        SELECT f.rule_id                                   AS rule_id,
               COUNT(*)                                    AS occurrences,
               COUNT(DISTINCT f.fingerprint)               AS unique_findings,
               COUNT(DISTINCT CASE WHEN last.verdict IS NOT NULL
                                   THEN f.fingerprint END) AS judged,
               COUNT(DISTINCT CASE WHEN last.verdict = 'true_positive'
                                   THEN f.fingerprint END) AS true_positives
        FROM report_findings f
        JOIN run_reports rr ON rr.project_id = f.project_id AND rr.run_id = f.run_id
        LEFT JOIN last ON last.project_id = f.project_id AND last.fingerprint = f.fingerprint
        WHERE rr.started_at >= datetime('now', ?){project_clause}
        GROUP BY f.rule_id
        ORDER BY occurrences DESC, rule_id
        """,
        params,
    ).fetchall()
    return [
        RuleStats(
            rule_id=str(row["rule_id"]),
            occurrences=int(row["occurrences"]),
            unique_findings=int(row["unique_findings"]),
            judged=int(row["judged"]),
            true_positives=int(row["true_positives"]),
        )
        for row in rows
    ]
