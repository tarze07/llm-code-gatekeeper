"""Import raportu JSON: rozpoznanie formatu, walidacja, redakcja, indeks.

To jest granica zaufania panelu. Plik raportu może być dowolny — od raportu
z sąsiedniego repozytorium po 200 MB przypadkowego JSON-a — więc wszystko
poniżej sprawdza *najpierw*, a wierzy dopiero potem (PLAN-WEB-UI.md §5, §8).

Nie zgadujemy brakujących danych. Raport bez pola `not_checked` ma pustą listę
ograniczeń i panel pokazuje „brak danych”, a nie „bramka niczego nie pomija”.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gatekeeper_core.core.finding import Verdict, compute_fingerprint

from .redaction import redact_text, redact_value

#: Format raportu z `gatekeeper run --format json` w wersji, która nie
#: numeruje formatu. Rozpoznawany po *braku* pola `report_version`.
FORMAT_V0 = "core/v0"
#: Zarezerwowane dla przyszłego raportu z jawnym `report_version: 1`.
FORMAT_V1 = "core/v1"

MAX_BYTES = 5 * 1024 * 1024
MAX_GATES = 200
MAX_FINDINGS = 5000
MAX_NOT_CHECKED = 200
MAX_FACTS_PER_GATE = 500
MAX_REASONS = 1000
MAX_TEXT_CHARS = 20_000

_VERDICTS = {v.value for v in Verdict}
_GATE_STATUSES = {"pass", "fail", "error", "skipped"}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_REASON_SOURCES = {"blocking", "threshold", "human_review", "gate_error"}


class ReportImportError(ValueError):
    """Raport odrzucony — komunikat jest przeznaczony dla operatora."""


@dataclass(frozen=True)
class ImportedReport:
    payload: dict[str, Any]
    index: dict[str, Any]
    content_hash: str
    format_version: str


def parse_report(raw: bytes) -> ImportedReport:
    """Sprawdza rozmiar, format i strukturę; zwraca gotowy do zapisu raport."""
    if len(raw) > MAX_BYTES:
        raise ReportImportError(
            f"raport ma {len(raw) // 1024} kB, limit to {MAX_BYTES // 1024} kB "
            "— to nie jest raport bramy albo trzeba podnieść limit świadomie"
        )
    if not raw.strip():
        raise ReportImportError("pusty plik")
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ReportImportError("plik nie jest tekstem UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ReportImportError(f"niepoprawny JSON: {exc.msg} (linia {exc.lineno})") from exc
    except (ValueError, RecursionError) as exc:
        raise ReportImportError("JSON zawiera zbyt dużą liczbę lub zbyt głęboką strukturę") from exc
    if not isinstance(data, dict):
        raise ReportImportError("raport musi być obiektem JSON, a nie listą ani liczbą")
    _validate_json_values(data)

    content_hash = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    format_version = _detect_format(data)
    payload = _adapt(data, format_version)
    _validate(payload)
    payload = _redact(payload)
    return ImportedReport(
        payload=payload,
        index=build_index(payload),
        content_hash=content_hash,
        format_version=format_version,
    )


def build_index(payload: dict[str, Any]) -> dict[str, Any]:
    """Zdenormalizowane pola do filtrowania historii — bez czytania raportu."""
    gates = payload["gates"]
    return {
        "verdict": payload["decision"]["verdict"],
        "started_at": payload["started_at"],
        "duration_s": payload.get("duration_s"),
        "base_sha": payload["base_sha"],
        "head_sha": payload["head_sha"],
        "repo": payload.get("repo"),
        "policy_version": payload.get("policy_version"),
        "gate_count": len(gates),
        "gate_error_count": sum(1 for g in gates if g["status"] == "error"),
        "finding_count": sum(len(g["findings"]) for g in gates),
    }


# ----------------------------------------------------------------- format


def _detect_format(data: dict[str, Any]) -> str:
    version = data.get("report_version")
    if version is None:
        # Historyczny raport core'a nie numeruje formatu. To jest ten adapter
        # „jawnie obsługujący stary format”, o który prosi plan §5 — a nie
        # ciche założenie, że każdy JSON bez wersji jest zgodny.
        return FORMAT_V0
    if str(version) == "1":
        return FORMAT_V1
    raise ReportImportError(
        f"nieobsługiwana wersja formatu raportu: {version!r} — ten panel zna "
        f"{FORMAT_V0} (brak pola) oraz {FORMAT_V1}"
    )


def _adapt(data: dict[str, Any], format_version: str) -> dict[str, Any]:
    """Sprowadza oba znane formaty do jednego kształtu, bez dopisywania treści."""
    decision = data.get("decision")
    if not isinstance(decision, dict):
        raise ReportImportError("brak sekcji `decision` — to nie jest raport bramy jakości")
    gates = data.get("gates")
    if not isinstance(gates, list):
        raise ReportImportError("brak listy `gates`")

    adapted = dict(data)
    adapted["decision"] = {
        "verdict": decision.get("verdict"),
        "reasons": _list(decision.get("reasons"), "decision.reasons"),
        "warnings": _list(decision.get("warnings"), "decision.warnings"),
        "suppressed": _list(decision.get("suppressed"), "decision.suppressed"),
    }
    adapted["not_checked"] = _list(data.get("not_checked"), "not_checked")
    adapted["gates"] = [_adapt_gate(g) for g in gates]
    return adapted


def _adapt_gate(gate: Any) -> dict[str, Any]:
    if not isinstance(gate, dict):
        raise ReportImportError("wpis w `gates` nie jest obiektem")
    adapted = dict(gate)
    adapted["facts"] = _object(gate.get("facts"), "facts")
    adapted["findings"] = [_adapt_finding(f, str(gate.get("gate", ""))) for f in
                           _list(gate.get("findings"), "findings")]
    adapted["warn_only"] = gate.get("warn_only", False)
    _optional_text(gate.get("message"), "message")
    adapted["message"] = gate.get("message") or ""
    # Ścieżki artefaktów bramek są tymczasowe i nie da się ich pobrać przez
    # panel — nie udajemy, że są linkiem (plan §8).
    adapted.pop("artifacts", None)
    return adapted


def _adapt_finding(finding: Any, gate_id: str) -> dict[str, Any]:
    if not isinstance(finding, dict):
        raise ReportImportError("wpis w `findings` nie jest obiektem")
    adapted = dict(finding)
    adapted.setdefault("gate", gate_id)
    adapted["evidence"] = _object(finding.get("evidence"), "evidence")
    adapted.setdefault("confidence", 1.0)
    _validate_finding(adapted, gate_id)
    if not adapted.get("fingerprint"):
        # Ta sama funkcja co w core — inaczej ocena znaleziska z panelu
        # wskazywałaby inny obiekt niż `gatekeeper verdict` z CLI.
        snippet = adapted["evidence"].get("snippet") or adapted.get("title") or ""
        adapted["fingerprint"] = compute_fingerprint(
            str(adapted.get("rule_id", "")), adapted.get("file"), str(snippet)
        )
    return adapted


# -------------------------------------------------------------- walidacja


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportImportError(f"`{field}` nie jest listą")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportImportError(f"`{field}` nie jest obiektem")
    return value


def _optional_text(value: Any, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ReportImportError(f"`{field}` nie jest tekstem")


def _identifier(value: Any, field: str) -> None:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 500
        or re.search(r"[\x00-\x1f\x7f/\\?#%]", value) or value in (".", "..")
    ):
        raise ReportImportError(f"`{field}` nie jest poprawnym identyfikatorem")


def _validate_json_values(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ReportImportError("zbyt głęboko zagnieżdżony JSON")
    if isinstance(value, dict):
        for child in value.values():
            _validate_json_values(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_values(child, depth + 1)
    elif isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ReportImportError("JSON zawiera liczbę poza skończonym zakresem")


def _duration(value: Any, field: str) -> None:
    if value is not None and (
        not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
    ):
        raise ReportImportError(f"`{field}` nie jest nieujemną liczbą")


def _validate(payload: dict[str, Any]) -> None:
    for key in ("run_id", "base_sha", "head_sha", "started_at"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ReportImportError(f"brak wymaganego pola `{key}`")
        if len(value) > 500:
            raise ReportImportError(f"pole `{key}` jest absurdalnie długie")
    _identifier(payload["run_id"], "run_id")
    _optional_text(payload.get("repo"), "repo")
    try:
        datetime.fromisoformat(payload["started_at"])
    except ValueError as exc:
        raise ReportImportError("`started_at` nie jest datą ISO 8601") from exc

    verdict = payload["decision"]["verdict"]
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        raise ReportImportError(
            f"nieznana decyzja polityki: {verdict!r} — dozwolone: {', '.join(sorted(_VERDICTS))}"
        )

    _duration(payload.get("duration_s"), "duration_s")
    policy_version = payload.get("policy_version")
    if policy_version is not None and (
        not isinstance(policy_version, int) or isinstance(policy_version, bool)
        or not 0 <= policy_version < 2**63
    ):
        raise ReportImportError("`policy_version` nie jest liczbą całkowitą")

    gates = payload["gates"]
    if len(gates) > MAX_GATES:
        raise ReportImportError(f"raport deklaruje {len(gates)} bramek, limit to {MAX_GATES}")

    findings_total = 0
    seen_gates: set[str] = set()
    for gate in gates:
        gate_id = gate.get("gate")
        if not isinstance(gate_id, str) or not gate_id.strip():
            raise ReportImportError("bramka bez identyfikatora")
        if gate_id in seen_gates:
            raise ReportImportError(f"bramka {gate_id!r} występuje w raporcie dwa razy")
        seen_gates.add(gate_id)
        if not isinstance(gate.get("status"), str) or gate["status"] not in _GATE_STATUSES:
            raise ReportImportError(
                f"{gate_id}: nieznany status bramki {gate.get('status')!r} "
                f"— dozwolone: {', '.join(sorted(_GATE_STATUSES))}"
            )
        _duration(gate.get("duration_s"), f"{gate_id}.duration_s")
        if not isinstance(gate["warn_only"], bool):
            raise ReportImportError(f"{gate_id}: `warn_only` nie jest wartością logiczną")
        if len(gate["facts"]) > MAX_FACTS_PER_GATE:
            raise ReportImportError(f"{gate_id}: zbyt wiele faktów")
        findings_total += len(gate["findings"])
        for finding in gate["findings"]:
            _validate_finding(finding, gate_id)

    if findings_total > MAX_FINDINGS:
        raise ReportImportError(
            f"raport ma {findings_total} znalezisk, limit to {MAX_FINDINGS}"
        )

    for section in ("reasons", "warnings", "suppressed"):
        entries = payload["decision"][section]
        if len(entries) > MAX_REASONS:
            raise ReportImportError(f"sekcja `{section}` ma zbyt wiele wpisów")
        for entry in entries:
            _validate_reason(entry, section)

    not_checked = payload["not_checked"]
    if len(not_checked) > MAX_NOT_CHECKED:
        raise ReportImportError("lista `not_checked` jest zbyt długa")
    for item in not_checked:
        if not isinstance(item, str):
            raise ReportImportError("`not_checked` zawiera wpis, który nie jest tekstem")


def _validate_finding(finding: dict[str, Any], gate_id: str) -> None:
    for key in ("rule_id", "title", "failure_scenario"):
        value = finding.get(key)
        if not isinstance(value, str) or not value.strip():
            # Ten sam wymóg co `Finding.__post_init__` w core: znalezisko bez
            # scenariusza awarii jest opinią, a nie znaleziskiem.
            raise ReportImportError(f"{gate_id}: znalezisko bez pola `{key}`")
        if len(value) > MAX_TEXT_CHARS:
            raise ReportImportError(f"{gate_id}: pole `{key}` przekracza {MAX_TEXT_CHARS} znaków")
    if not isinstance(finding.get("severity"), str) or finding["severity"] not in _SEVERITIES:
        raise ReportImportError(
            f"{gate_id}: nieznana waga {finding.get('severity')!r} w {finding.get('rule_id')!r}"
        )
    line = finding.get("line")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 0):
        raise ReportImportError(f"{gate_id}: `line` nie jest numerem linii")
    file = finding.get("file")
    if file is not None and not isinstance(file, str):
        raise ReportImportError(f"{gate_id}: `file` nie jest ścieżką")
    confidence = finding.get("confidence")
    if (
        not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise ReportImportError(f"{gate_id}: `confidence` nie jest liczbą od 0 do 1")
    if finding.get("gate") != gate_id:
        raise ReportImportError(f"{gate_id}: znalezisko wskazuje inną bramkę")
    fingerprint = finding.get("fingerprint")
    if fingerprint is not None and fingerprint != "":
        _identifier(fingerprint, "fingerprint")


def _validate_reason(reason: Any, section: str) -> None:
    if not isinstance(reason, dict):
        raise ReportImportError(f"wpis w `{section}` nie jest obiektem")
    if not isinstance(reason.get("source"), str) or reason["source"] not in _REASON_SOURCES:
        raise ReportImportError(
            f"`{section}`: nieznane źródło powodu {reason.get('source')!r}"
        )
    if not isinstance(reason.get("rule"), str) or not reason["rule"].strip():
        raise ReportImportError(f"`{section}`: powód bez reguły")
    _optional_text(reason.get("gate"), f"{section}.gate")
    _optional_text(reason.get("detail"), f"{section}.detail")
    for fingerprint in _list(reason.get("fingerprints"), f"{section}.fingerprints"):
        _identifier(fingerprint, f"{section}.fingerprints")


# --------------------------------------------------------------- redakcja


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["gates"] = []
    for gate in payload["gates"]:
        redacted_gate = dict(gate)
        redacted_gate["message"] = redact_text(str(gate["message"]))
        redacted_gate["facts"] = {k: redact_value(v, str(k)) for k, v in gate["facts"].items()}
        redacted_gate["findings"] = [_redact_finding(f) for f in gate["findings"]]
        out["gates"].append(redacted_gate)
    decision = dict(payload["decision"])
    for section in ("reasons", "warnings", "suppressed"):
        decision[section] = [
            {**r, "detail": redact_text(str(r.get("detail") or ""))} for r in decision[section]
        ]
    out["decision"] = decision
    out["not_checked"] = [redact_text(str(item)) for item in payload["not_checked"]]
    return out


def _redact_finding(finding: dict[str, Any]) -> dict[str, Any]:
    out = dict(finding)
    out["title"] = redact_text(str(finding["title"]))
    out["failure_scenario"] = redact_text(str(finding["failure_scenario"]))
    out["evidence"] = {k: redact_value(v, str(k)) for k, v in finding["evidence"].items()}
    return out
