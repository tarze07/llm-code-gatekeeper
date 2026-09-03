"""Linter jakości testów C# przez `gatekeeper-cs-helper lint` — odpowiednik
`testing/quality.py` w python-packu (tam: `ast`, tu: Roslyn w podprocesie,
PLAN-G2.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gatekeeper_core.core.finding import Severity

from .discovery import HELPER, HelperUnavailable, TestItem, run_helper


@dataclass(frozen=True)
class QualityIssue:
    """Kształt zgodny z `core.plugins.QualityIssue`."""

    nodeid: str
    rule_id: str
    severity: Severity
    title: str
    failure_scenario: str
    evidence: dict[str, Any]


def lint_quality(root: Path, relative_paths: list[str]) -> dict[str, list[QualityIssue]]:
    """Mapa nodeid → znaleziska jakości dla podanych plików testowych."""
    payload = run_helper("lint", root, relative_paths)
    out: dict[str, list[QualityIssue]] = {}
    for raw in payload.get("issues", []) or []:
        issue = QualityIssue(
            nodeid=raw["nodeid"],
            rule_id=raw["rule_id"],
            severity=Severity.parse(raw["severity"]),
            title=raw["title"],
            failure_scenario=raw["failure_scenario"],
            evidence=raw.get("evidence") or {},
        )
        out.setdefault(issue.nodeid, []).append(issue)
    return out


__all__ = ["QualityIssue", "lint_quality", "HELPER", "HelperUnavailable", "TestItem"]
