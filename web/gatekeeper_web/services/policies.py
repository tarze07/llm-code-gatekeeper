"""Walidacja, podgląd i porównanie wersji polityki.

Cała logika oceny należy do core'a: panel nie ma własnego parsera progów ani
własnego pojęcia „reguły blokującej". Tutaj jest tylko to, czego core nie
robi, bo nie musi — zapisanie snapshotu na dysk dla bramek i pokazanie
operatorowi, **co się zmieni** po aktywacji (PLAN-WEB-UI.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from gatekeeper_core.core.policy import Policy, PolicyError
from gatekeeper_core.gates import known_facts, known_gate_ids

from ..storage import PolicyRevision

POLICY_FILENAME = "gates.yaml"
EXCEPTIONS_FILENAME = "exceptions.yaml"
SCOPE_MAP_FILENAME = "scope_map.yaml"

#: Snapshot polityki to plik konfiguracyjny, nie repozytorium. Milion linii
#: YAML-a oznacza pomyłkę, a nie wyjątkowo bogatą politykę.
MAX_YAML_BYTES = 256 * 1024


class PolicyInputError(ValueError):
    pass


#: Katalog z polityką startową w danych pakietu. Nie czytamy `core/policy/`:
#: tamten plik jest kalibracją core'a i zmienia się razem z zależnością,
#: a profil w panelu ma oceniać tym samym, czym oceniał wczoraj.
STARTER_PACKAGE = "gatekeeper_web.polityka_startowa"


@dataclass(frozen=True)
class StarterPolicy:
    """Treść pierwszego szkicu — to, co operator widzi zamiast pustego pola."""

    policy_yaml: str
    exceptions_yaml: str
    scope_map_yaml: str


def starter_policy() -> StarterPolicy:
    """Polityka startowa z danych pakietu.

    Nowy profil dostaje ją zamiast `version: 1`. Pusty szkic przechodził
    walidację i dawał profil bez jednej reguły blokującej — formalnie poprawny,
    w praktyce brama, która nie bramkuje. Operator, który chciał czegokolwiek
    innego, musiał znaleźć `gates.yaml` na dysku i wkleić go ręcznie.
    """

    def read(name: str) -> str:
        return resources.files(STARTER_PACKAGE).joinpath(name).read_text(encoding="utf-8")

    return StarterPolicy(
        policy_yaml=read(POLICY_FILENAME),
        exceptions_yaml=read(EXCEPTIONS_FILENAME),
        scope_map_yaml=read(SCOPE_MAP_FILENAME),
    )


@dataclass
class MaterializedPolicy:
    """Snapshot zapisany na dysku — dokładnie to, co dostaną bramki."""

    directory: Path
    policy_path: Path
    exceptions_path: Path | None
    scope_map_path: Path | None


@dataclass
class PolicyValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    expiring: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyChange:
    kind: str
    detail: str
    #: `True`, gdy zmiana **zmniejsza** rygor — to jest ta lista, którą trzeba
    #: przeczytać przed aktywacją.
    loosens: bool = False


def check_yaml_size(name: str, text: str | None) -> None:
    if text is not None and len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise PolicyInputError(f"{name}: plik przekracza {MAX_YAML_BYTES // 1024} kB")


def materialize(revision: PolicyRevision, directory: Path) -> MaterializedPolicy:
    """Zapisuje snapshot wersji do katalogu i zwraca ścieżki.

    `Policy.load()` szuka `exceptions.yaml` obok pliku polityki, więc nazwy
    plików są takie same jak w repozytorium — snapshot ma się zachowywać
    dokładnie jak polityka z dysku.
    """
    directory.mkdir(parents=True, exist_ok=True)
    policy_path = directory / POLICY_FILENAME
    policy_path.write_text(revision.policy_yaml, encoding="utf-8")

    exceptions_path: Path | None = None
    if revision.exceptions_yaml:
        exceptions_path = directory / EXCEPTIONS_FILENAME
        exceptions_path.write_text(revision.exceptions_yaml, encoding="utf-8")

    scope_map_path: Path | None = None
    if revision.scope_map_yaml:
        scope_map_path = directory / SCOPE_MAP_FILENAME
        scope_map_path.write_text(revision.scope_map_yaml, encoding="utf-8")

    return MaterializedPolicy(directory, policy_path, exceptions_path, scope_map_path)


def load_policy(revision: PolicyRevision, directory: Path) -> Policy:
    snapshot = materialize(revision, directory)
    return Policy.load(snapshot.policy_path, snapshot.exceptions_path)


def validate(revision: PolicyRevision, directory: Path) -> PolicyValidation:
    """Ten sam `Policy.load()` + `lint()`, co `gatekeeper policy lint`."""
    try:
        policy = load_policy(revision, directory)
    except (OSError, PolicyError) as exc:
        return PolicyValidation(ok=False, errors=[str(exc)])

    errors = policy.lint(known_facts(), known_gate_ids())
    today = date.today()
    return PolicyValidation(
        ok=not errors,
        errors=errors,
        expiring=[
            f"{ex.rule} ({ex.owner}) wygasa {ex.expires}"
            for ex in policy.expiring_exemptions(14)
        ],
        expired=[
            f"{ex.rule} ({ex.owner}) wygasł {ex.expires}"
            for ex in policy.exemptions
            if ex.is_expired(today)
        ],
        summary=summarize(policy),
    )


def summarize(policy: Policy) -> dict[str, Any]:
    return {
        "version": policy.version,
        "blocking": sorted(e.raw for e in policy.blocking),
        "thresholds": {
            t.fact: {
                "min": t.min,
                "max": t.max,
                "on_violation": t.on_violation,
                "message": t.message,
            }
            for t in policy.thresholds
        },
        "human_review": sorted(getattr(e, "raw", str(e)) for e in policy.human_review),
        "warn_only": sorted(policy.warn_only),
        "on_gate_error": policy.on_gate_error,
        "max_findings_reported": policy.max_findings_reported,
        "exemptions": [
            {
                "rule": ex.rule,
                "owner": ex.owner,
                "reason": ex.reason,
                "expires": ex.expires.isoformat(),
                "expired": ex.is_expired(),
                "fingerprints": list(ex.fingerprints),
            }
            for ex in policy.exemptions
        ],
        "gates": dict(sorted(policy.gates.items())),
    }


def compare(previous: dict[str, Any], candidate: dict[str, Any]) -> list[PolicyChange]:
    """Różnica dwóch podsumowań, z jawnym oznaczeniem rozluźnień.

    „Które blokady stają się ostrzeżeniami" to jedyne pytanie, które naprawdę
    trzeba zadać przed aktywacją nowej polityki.
    """
    changes: list[PolicyChange] = []

    old_blocking, new_blocking = set(previous["blocking"]), set(candidate["blocking"])
    for rule in sorted(new_blocking - old_blocking):
        changes.append(PolicyChange("reguła blokująca", f"dodano `{rule}`"))
    for rule in sorted(old_blocking - new_blocking):
        changes.append(
            PolicyChange("reguła blokująca", f"usunięto `{rule}`", loosens=True)
        )

    old_warn, new_warn = set(previous["warn_only"]), set(candidate["warn_only"])
    for gate in sorted(new_warn - old_warn):
        changes.append(
            PolicyChange(
                "warn_only",
                f"`{gate}` przestaje blokować — jej wynik będzie tylko ostrzeżeniem",
                loosens=True,
            )
        )
    for gate in sorted(old_warn - new_warn):
        changes.append(PolicyChange("warn_only", f"`{gate}` znów blokuje"))

    old_thresholds: dict[str, Any] = previous["thresholds"]
    new_thresholds: dict[str, Any] = candidate["thresholds"]
    for fact in sorted(set(new_thresholds) - set(old_thresholds)):
        changes.append(PolicyChange("próg", f"dodano próg `{fact}`"))
    for fact in sorted(set(old_thresholds) - set(new_thresholds)):
        changes.append(PolicyChange("próg", f"usunięto próg `{fact}`", loosens=True))
    for fact in sorted(set(old_thresholds) & set(new_thresholds)):
        changes.extend(_threshold_changes(fact, old_thresholds[fact], new_thresholds[fact]))

    if previous["on_gate_error"] != candidate["on_gate_error"]:
        changes.append(
            PolicyChange(
                "błąd bramki",
                f"{previous['on_gate_error']} → {candidate['on_gate_error']}",
                loosens=candidate["on_gate_error"] == "warn",
            )
        )

    old_ex = {(e["rule"], e["expires"]) for e in previous["exemptions"]}
    new_ex = {(e["rule"], e["expires"]) for e in candidate["exemptions"]}
    for rule, expires in sorted(new_ex - old_ex):
        changes.append(
            PolicyChange("wyjątek", f"dodano wyjątek `{rule}` do {expires}", loosens=True)
        )
    for rule, expires in sorted(old_ex - new_ex):
        changes.append(PolicyChange("wyjątek", f"usunięto wyjątek `{rule}` ({expires})"))
    return changes


_SEVERITY_ORDER = {"warn": 0, "review": 1, "block": 2}


def _threshold_changes(fact: str, old: dict[str, Any], new: dict[str, Any]) -> list[PolicyChange]:
    changes: list[PolicyChange] = []
    if old.get("max") != new.get("max"):
        loosens = _is_looser(old.get("max"), new.get("max"), higher_is_looser=True)
        changes.append(
            PolicyChange("próg", f"`{fact}` max: {old.get('max')} → {new.get('max')}", loosens)
        )
    if old.get("min") != new.get("min"):
        loosens = _is_looser(old.get("min"), new.get("min"), higher_is_looser=False)
        changes.append(
            PolicyChange("próg", f"`{fact}` min: {old.get('min')} → {new.get('min')}", loosens)
        )
    if old.get("on_violation") != new.get("on_violation"):
        before = _SEVERITY_ORDER.get(str(old.get("on_violation")), 2)
        after = _SEVERITY_ORDER.get(str(new.get("on_violation")), 2)
        changes.append(
            PolicyChange(
                "próg",
                f"`{fact}`: {old.get('on_violation')} → {new.get('on_violation')}",
                loosens=after < before,
            )
        )
    return changes


def _is_looser(old: Any, new: Any, higher_is_looser: bool) -> bool:
    if old is None or new is None:
        return new is None
    try:
        return float(new) > float(old) if higher_is_looser else float(new) < float(old)
    except (TypeError, ValueError):  # pragma: no cover
        return False
