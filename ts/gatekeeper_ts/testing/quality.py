"""Linter jakości testów TS/JS przez `helper.cjs lint` — odpowiednik
`testing/quality.py` w python-packu (tam: `ast`, tu: ESTree w podprocesie).

Pięć reguł i ich `rule_id` są **te same** co w Pythonie i C#
(`test.no_assertion`, `test.constant_assertion`, `test.mock_echo`,
`test.only_smoke`, `test.exception_swallowed`) — semantyka przełożona
z `assert`/`Assert.*` na `expect(...).matcher(...)`, bo w TS/JS asercja
to wywołanie matchera, nie słowo kluczowe ani metoda statyczna.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gatekeeper_core.core.finding import Severity

from .discovery import HelperUnavailable, TestItem, run_helper


@dataclass(frozen=True)
class QualityIssue:
    """Kształt zgodny z `gatekeeper_core.core.plugins.QualityIssue`."""

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


__all__ = ["HelperUnavailable", "QualityIssue", "TestItem", "lint_quality"]
