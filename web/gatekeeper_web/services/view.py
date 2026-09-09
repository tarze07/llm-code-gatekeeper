"""Model widoku: co ten wynik właściwie znaczy.

Najważniejsza rzecz, którą robi ten moduł, to rozdzielenie trzech poziomów,
mylonych w każdym „dashboardzie z zielonym kółkiem” (PLAN-WEB-UI.md §3):

| poziom    | pytanie                                   |
|-----------|-------------------------------------------|
| zadanie   | czy w ogóle udało się wykonać sprawdzenie |
| bramka    | jaki wynik oddała pojedyncza kontrola     |
| polityka  | jaka jest decyzja o ocenianej zmianie     |

Stąd bierze się przypadek, który musi być widoczny na pierwszym ekranie:
`G2.diff_coverage` ze statusem `pass` i jednocześnie naruszeniem progu
`coverage.diff_ratio`. Status bramki zostaje surowy, a konflikt jest nazwany
wprost, zamiast wybierać jedną z dwóch prawd.

Moduł nie zna listy identyfikatorów bramek — bierze je z raportu, więc bramka
dodana przez entry point pojawia się w panelu bez zmiany kodu (plan §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gatekeeper_core.core.finding import Finding, GateResult, Reason, RunResult, Severity, Verdict

BRAK_DANYCH = "brak danych"

JOB_STATE_LABELS = {
    "completed": "zakończone",
    "imported": "zaimportowane",
    "queued": "w kolejce",
    "preparing": "przygotowanie",
    "running": "trwa",
    "cancelling": "zatrzymywanie",
    "cancelled": "anulowane",
    "interrupted": "przerwane",
    "failed": "awaria",
}

GATE_STATUS_LABELS = {
    "pass": "przeszła",
    "fail": "nie przeszła",
    "error": "błąd wykonania",
    "skipped": "pominięta",
}

VERDICT_LABELS = {
    Verdict.PASS: "PASS — zmiana przechodzi",
    Verdict.PASS_WITH_REVIEW: "PASS-WITH-REVIEW — wymaga człowieka",
    Verdict.BLOCK: "BLOCK — zmiana zatrzymana",
}

SEVERITY_LABELS = {
    Severity.CRITICAL: "krytyczna",
    Severity.HIGH: "wysoka",
    Severity.MEDIUM: "średnia",
    Severity.LOW: "niska",
    Severity.INFO: "informacyjna",
}

REASON_SOURCE_LABELS = {
    "blocking": "reguła blokująca",
    "threshold": "próg polityki",
    "human_review": "skierowanie do człowieka",
    "gate_error": "błąd bramki",
}

#: Fakty wyrażone ułamkiem 0–1. Dobrane po nazwie faktu, nie po bramce —
#: nowa bramka z faktem `*.ratio` dostaje tę samą prezentację za darmo.
_RATIO_SUFFIXES = (".diff_ratio", ".agent_ratio", "_ratio")

#: Fakt → fakty, które go objaśniają (licznik i mianownik pomiaru).
#: Bez tego „pokrycie 2,7%” nie mówi, czy to 1/37 linii, czy 27/1000.
_FACT_DETAIL = {
    "coverage.diff_ratio": ("coverage.covered_lines", "coverage.total_lines"),
}


@dataclass(frozen=True)
class FactView:
    key: str
    display: str
    missing: bool = False
    detail: str = ""

    @property
    def is_missing(self) -> bool:
        return self.missing


@dataclass(frozen=True)
class ReasonView:
    section: str
    source: str
    source_label: str
    rule: str
    detail: str
    gate: str | None
    fingerprints: tuple[str, ...]
    fact: FactView | None = None

    @property
    def blocks(self) -> bool:
        return self.section == "reasons" and self.source in ("blocking", "threshold")


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    value: str


@dataclass(frozen=True)
class FindingView:
    gate: str
    rule_id: str
    severity: str
    severity_label: str
    title: str
    failure_scenario: str
    file: str | None
    line: int | None
    location: str
    confidence: float
    fingerprint: str
    evidence: tuple[EvidenceItem, ...]

    @property
    def has_location(self) -> bool:
        return self.file is not None


@dataclass(frozen=True)
class GateView:
    gate: str
    status: str
    status_label: str
    warn_only: bool
    message: str
    duration_s: float | None
    facts: tuple[FactView, ...]
    findings: tuple[FindingView, ...]
    reasons: tuple[ReasonView, ...]
    notes: tuple[str, ...]

    @property
    def blocking_reasons(self) -> tuple[ReasonView, ...]:
        return tuple(r for r in self.reasons if r.blocks)

    @property
    def needs_explanation(self) -> bool:
        """Status bramki i decyzja polityki mówią co innego."""
        return bool(self.notes)


@dataclass
class RunView:
    run_id: str
    project_id: int
    project_slug: str
    project_name: str
    origin: str
    job_state: str
    job_state_label: str
    verdict: str
    verdict_label: str
    repo: str | None
    base_sha: str
    head_sha: str
    started_at: datetime
    duration_s: float | None
    policy_version: int | None
    gates: tuple[GateView, ...]
    findings: tuple[FindingView, ...]
    reasons: tuple[ReasonView, ...]
    warnings: tuple[ReasonView, ...]
    suppressed: tuple[ReasonView, ...]
    not_checked: tuple[str, ...]
    unattached_reasons: tuple[ReasonView, ...] = field(default_factory=tuple)

    # --------------------------------------------------------- podsumowania

    @property
    def severity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    @property
    def gate_status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for g in self.gates:
            counts[g.status] = counts.get(g.status, 0) + 1
        return counts

    @property
    def conflicting_gates(self) -> tuple[GateView, ...]:
        return tuple(g for g in self.gates if g.needs_explanation)

    @property
    def has_gate_errors(self) -> bool:
        return any(g.status == "error" for g in self.gates)

    def find_finding(self, fingerprint: str) -> FindingView | None:
        return next((f for f in self.findings if f.fingerprint == fingerprint), None)


def build_run_view(
    run: RunResult,
    *,
    project_id: int,
    project_slug: str,
    project_name: str,
    origin: str = "imported",
) -> RunView:
    facts = run.facts
    reasons = tuple(_reason_view(r, facts, "reasons") for r in run.decision.reasons)
    warnings = tuple(_reason_view(r, facts, "warnings") for r in run.decision.warnings)
    suppressed = tuple(_reason_view(r, facts, "suppressed") for r in run.decision.suppressed)
    all_reasons = reasons + warnings + suppressed

    gates = tuple(_gate_view(g, all_reasons) for g in run.gate_results)
    findings = tuple(
        _finding_view(f) for f in sorted(run.findings, key=lambda f: f.weight, reverse=True)
    )
    gate_ids = {g.gate for g in run.gate_results}
    verdict = run.decision.verdict

    return RunView(
        run_id=run.run_id,
        project_id=project_id,
        project_slug=project_slug,
        project_name=project_name,
        origin=origin,
        # Raport zaimportowany opisuje zadanie, które gdzieś już się skończyło.
        # Panel nie ma prawa twierdzić, że sam je wykonał.
        job_state="imported" if origin == "imported" else "completed",
        job_state_label=(
            JOB_STATE_LABELS["imported"] if origin == "imported"
            else JOB_STATE_LABELS["completed"]
        ),
        verdict=verdict.value,
        verdict_label=VERDICT_LABELS[verdict],
        repo=run.repo or None,
        base_sha=run.base_sha,
        head_sha=run.head_sha,
        started_at=run.started_at,
        duration_s=run.duration_s,
        policy_version=run.policy_version or None,
        gates=gates,
        findings=findings,
        reasons=reasons,
        warnings=warnings,
        suppressed=suppressed,
        not_checked=tuple(run.not_checked),
        unattached_reasons=tuple(
            r for r in all_reasons if r.gate is None or r.gate not in gate_ids
        ),
    )


# ------------------------------------------------------------------ bramki


def _gate_view(gate: GateResult, all_reasons: tuple[ReasonView, ...]) -> GateView:
    reasons = tuple(r for r in all_reasons if r.gate == gate.gate)
    return GateView(
        gate=gate.gate,
        status=gate.status,
        status_label=GATE_STATUS_LABELS.get(gate.status, gate.status),
        warn_only=gate.warn_only,
        message=gate.message,
        duration_s=gate.duration_s,
        facts=tuple(describe_fact(k, gate.facts) for k in sorted(gate.facts)),
        findings=tuple(
            _finding_view(f) for f in sorted(gate.findings, key=lambda f: f.weight, reverse=True)
        ),
        reasons=reasons,
        notes=_gate_notes(gate, reasons),
    )


def _gate_notes(gate: GateResult, reasons: tuple[ReasonView, ...]) -> tuple[str, ...]:
    """Zdania, bez których sam status bramki wprowadza w błąd."""
    notes: list[str] = []
    blocking = [r for r in reasons if r.blocks]
    if gate.status == "pass" and blocking:
        rules = ", ".join(f"`{r.rule}`" for r in blocking)
        notes.append(
            f"Bramka oddała status `pass` (wykonała się bez błędu), ale polityka uznała "
            f"jej pomiar za naruszenie: {rules}. To nie jest sprzeczność — bramka mierzy, "
            f"polityka decyduje."
        )
    if gate.warn_only and gate.status in ("fail", "error"):
        notes.append(
            "Bramka jest oznaczona jako `warn_only`, więc jej wynik nie blokuje zmiany. "
            "Dowodu, którego nie dostarczyła, to nie zastępuje."
        )
    if gate.status == "error":
        notes.append(
            "Bramka nie policzyła wyniku. Brak znalezisk w tej sekcji oznacza brak pomiaru, "
            "a nie brak problemów."
        )
    if gate.status == "skipped":
        notes.append("Bramka nie została uruchomiona w tym przebiegu.")
    return tuple(notes)


# ------------------------------------------------------------------- fakty


def describe_fact(key: str, facts: dict[str, Any]) -> FactView:
    """Formatuje fakt. Brak faktu to `brak danych`, nigdy zero."""
    if key not in facts or facts[key] is None:
        return FactView(key=key, display=BRAK_DANYCH, missing=True)
    value = facts[key]
    return FactView(key=key, display=format_fact_value(key, value), detail=_fact_detail(key, facts))


def format_fact_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "tak" if value else "nie"
    if isinstance(value, (int, float)) and key.endswith(_RATIO_SUFFIXES):
        return _percent(float(value))
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "pusta lista"
        return f"{len(value)}: " + ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k} = {v}" for k, v in sorted(value.items())) or "pusty obiekt"
    return str(value)


def _fact_detail(key: str, facts: dict[str, Any]) -> str:
    pair = _FACT_DETAIL.get(key)
    if not pair:
        return ""
    numerator, denominator = (facts.get(pair[0]), facts.get(pair[1]))
    if numerator is None or denominator is None:
        return f"{BRAK_DANYCH}: {pair[0]} / {pair[1]}"
    return f"{numerator} z {denominator}"


def _percent(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + "%"


# ------------------------------------------------------------------ powody


def _reason_view(reason: Reason, facts: dict[str, Any], section: str) -> ReasonView:
    # Reguła polityki nazywa się dokładnie tak, jak fakt, na którym operuje
    # (`coverage.diff_ratio`), więc pomiar da się pokazać obok decyzji.
    fact = describe_fact(reason.rule, facts) if reason.rule in facts else None
    label = REASON_SOURCE_LABELS.get(reason.source, reason.source)
    if section == "warnings":
        label = f"ostrzeżenie — {label}"
    elif section == "suppressed":
        label = f"wyciszone wyjątkiem — {label}"
    return ReasonView(
        section=section,
        source=reason.source,
        source_label=label,
        rule=reason.rule,
        detail=reason.detail,
        gate=reason.gate,
        fingerprints=tuple(reason.fingerprints),
        fact=fact,
    )


# -------------------------------------------------------------- znaleziska


def _finding_view(finding: Finding) -> FindingView:
    return FindingView(
        gate=finding.gate,
        rule_id=finding.rule_id,
        severity=finding.severity.value,
        severity_label=SEVERITY_LABELS.get(finding.severity, finding.severity.value),
        title=finding.title,
        failure_scenario=finding.failure_scenario,
        file=finding.file,
        line=finding.line,
        location=finding.location,
        confidence=finding.confidence,
        fingerprint=finding.fingerprint,
        evidence=tuple(
            EvidenceItem(key=str(k), value=format_fact_value(str(k), v))
            for k, v in sorted(finding.evidence.items())
        ),
    )
