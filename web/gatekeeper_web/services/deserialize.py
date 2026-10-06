"""Adapter: zapisany raport JSON → obiekty `core`.

Panel nie ma własnej kopii modelu wyniku. Widok, eksport Markdown i eksport
HTML pracują na `RunResult` z `gatekeeper_core`, dzięki czemu raport
zaimportowany i raport policzony przez silnik przechodzą tę samą ścieżkę
renderowania (PLAN-WEB-UI.md §7: „surowe raporty zachowują oryginalne pola
i mają opisany adapter do modelu widoków”).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gatekeeper_core.core.finding import (
    Decision,
    Finding,
    GateResult,
    GateStatus,
    Reason,
    ReasonSource,
    RunResult,
    Severity,
    Verdict,
)


def run_result_from_payload(payload: dict[str, Any]) -> RunResult:
    return RunResult(
        run_id=str(payload["run_id"]),
        repo=str(payload.get("repo") or ""),
        base_sha=str(payload["base_sha"]),
        head_sha=str(payload["head_sha"]),
        gate_results=[_gate(g) for g in payload["gates"]],
        decision=_decision(payload["decision"]),
        started_at=_timestamp(payload["started_at"]),
        duration_s=_duration(payload.get("duration_s")),
        policy_version=int(payload.get("policy_version") or 0),
        not_checked=[str(x) for x in payload.get("not_checked") or []],
    )


def _gate(data: dict[str, Any]) -> GateResult:
    status: GateStatus = data["status"]
    return GateResult(
        gate=str(data["gate"]),
        status=status,
        duration_s=_duration(data.get("duration_s")),
        findings=[_finding(f) for f in data.get("findings") or []],
        facts=dict(data.get("facts") or {}),
        message=str(data.get("message") or ""),
        warn_only=bool(data.get("warn_only")),
    )


def _finding(data: dict[str, Any]) -> Finding:
    return Finding(
        gate=str(data["gate"]),
        rule_id=str(data["rule_id"]),
        severity=Severity.parse(str(data["severity"])),
        title=str(data["title"]),
        failure_scenario=str(data["failure_scenario"]),
        file=data.get("file"),
        line=data.get("line"),
        confidence=float(data.get("confidence", 1.0)),
        evidence=dict(data.get("evidence") or {}),
        # Fingerprint z raportu, nie liczony na nowo: ocena znaleziska w panelu
        # musi wskazywać ten sam obiekt co `gatekeeper verdict`.
        fingerprint=str(data.get("fingerprint") or ""),
    )


def _decision(data: dict[str, Any]) -> Decision:
    return Decision(
        verdict=Verdict(str(data["verdict"])),
        reasons=[_reason(r) for r in data.get("reasons") or []],
        warnings=[_reason(r) for r in data.get("warnings") or []],
        suppressed=[_reason(r) for r in data.get("suppressed") or []],
    )


def _reason(data: dict[str, Any]) -> Reason:
    source: ReasonSource = data["source"]
    return Reason(
        source=source,
        rule=str(data["rule"]),
        detail=str(data.get("detail") or ""),
        gate=data.get("gate"),
        fingerprints=tuple(str(f) for f in data.get("fingerprints") or ()),
    )


def _timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _duration(value: Any) -> float | None:
    return None if value is None else float(value)
