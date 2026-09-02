"""G1 — złożoność cyklomatyczna (McCabe) na poziomie metody, tylko na
funkcjach, których ciało przecina diff. Plan i uzasadnienie architektoniczne:
`PLAN-G1-complexity.md`.

Nie jest to defekt jak `G1.static` (nieistniejące API, błąd kompilacji) —
to kształt, który mnoży ścieżki do przetestowania i chowa błędy agenta
w zagnieżdżeniu (Uncle Bob, `uncle-bob-gauntlet.md`). Osobna bramka, osobna
dźwignia polityki (nie `warn_only` razem z typami) — `PLAN-G1-complexity.md`
§1 rozstrzyga to jawnie, nie przez przeoczenie.

Ta bramka sama nie ma żadnej logiki językowej — jest agregatorem poziomu 1
(`core/plugins.py`): pętla po zainstalowanych `ComplexityAnalyzer`
(`gatekeeper.complexity_analyzers`; dziś: `PythonComplexityAnalyzer`,
`TsComplexityAnalyzer`, `CsharpComplexityAnalyzer`). Brak zainstalowanego
analizatora to `skipped`, nie błąd — bramka nie ma czego mierzyć, jeśli
żaden dostawca nie umie parsować danego języka.
"""

from __future__ import annotations

import time
from importlib.metadata import entry_points
from typing import Any

from ..core.change import ChangeContext
from ..core.finding import Finding, GateResult, Severity
from ..core.plugins import ComplexityAnalyzer, MethodComplexity
from . import Gate, register

COMPLEXITY_ANALYZER_GROUP = "gatekeeper.complexity_analyzers"

#: Próg severity — M ≥ 21 to `high`, 11–20 to `medium` (PLAN-G1-complexity.md §5.2).
_HIGH_SEVERITY_THRESHOLD = 21


def _installed_analyzers() -> list[ComplexityAnalyzer]:
    return [ep.load()() for ep in entry_points(group=COMPLEXITY_ANALYZER_GROUP)]


@register
class ComplexityGuard(Gate):
    id = "G1.complexity"
    name = "Złożoność cyklomatyczna (McCabe)"
    budget_s = 30.0

    @classmethod
    def declared_facts(cls) -> tuple[str, ...]:
        facts: set[str] = {
            "complexity.max",
            "complexity.over_threshold_count",
            "complexity.methods_measured",
        }
        for analyzer in _installed_analyzers():
            facts.update(analyzer.empty_facts())
        return tuple(sorted(facts))

    def run(self, change: ChangeContext) -> GateResult:
        started = time.monotonic()
        analyzers = _installed_analyzers()
        facts: dict[str, Any] = {
            "complexity.max": 0,
            "complexity.over_threshold_count": 0,
            "complexity.methods_measured": 0,
        }
        for analyzer in analyzers:
            facts.update(analyzer.empty_facts())

        if not analyzers:
            return self.result(
                status="skipped",
                duration_s=time.monotonic() - started,
                facts=facts,
                message="brak zainstalowanego analizatora złożoności "
                f"(entry points `{COMPLEXITY_ANALYZER_GROUP}`)",
            )

        max_method = int(self.config.get("max_method", 10))
        include_tests = bool(self.config.get("include_tests", False))

        methods: list[MethodComplexity] = []
        for analyzer in analyzers:
            outcome = analyzer.analyze(change, self.config, self.id, self.budget_s)
            facts.update(outcome.facts)
            if outcome.error is not None:
                return self.result(
                    status="error",
                    duration_s=time.monotonic() - started,
                    facts=facts,
                    message=outcome.error,
                )
            methods.extend(outcome.methods)

        measured = self._in_scope(change, methods, include_tests)
        facts["complexity.methods_measured"] = len(measured)
        facts["complexity.max"] = max((m.complexity for m in measured), default=0)

        findings = [
            self._finding(change, m, max_method) for m in measured if m.complexity > max_method
        ]
        facts["complexity.over_threshold_count"] = len(findings)

        return self.result(
            status="fail" if findings else "pass",
            duration_s=time.monotonic() - started,
            facts=facts,
            findings=findings,
            message=f"{len(measured)} zmierzonych metod w diffie, {len(findings)} powyżej progu "
            f"{max_method} (max M = {facts['complexity.max']})",
        )

    def _in_scope(
        self, change: ChangeContext, methods: list[MethodComplexity], include_tests: bool
    ) -> list[MethodComplexity]:
        """Tylko metody, których ciało przecina diff — nowa funkcja liczy się
        zawsze (całe jej ciało jest „dodane"), nietknięta stara funkcja w tym
        samym pliku milczy, nawet jeśli plik ma inne zmiany gdzie indziej
        (`only_changed_lines` mierzy linie, to mierzy metody — PLAN-G1-complexity.md §4)."""
        out = []
        for method in methods:
            if not include_tests and change.is_test_file(method.file):
                continue
            added = change.added_lines(method.file)
            if added & set(range(method.lineno, method.end_lineno + 1)):
                out.append(method)
        return out

    def _finding(
        self, change: ChangeContext, method: MethodComplexity, threshold: int
    ) -> Finding:
        severity = (
            Severity.HIGH if method.complexity >= _HIGH_SEVERITY_THRESHOLD else Severity.MEDIUM
        )
        snippet = self._signature_snippet(change, method)
        return Finding(
            gate=self.id,
            rule_id="complexity.too_high",
            severity=severity,
            title=f"{method.name} ma złożoność cyklomatyczną {method.complexity} "
            f"(próg {threshold})",
            failure_scenario=(
                f"Funkcja `{method.name}` ma {method.complexity} liniowo niezależnych ścieżek "
                f"(próg {threshold}). Agent łatwo zostawia nieprzetestowaną gałąź w zagnieżdżeniu; "
                "rozbij warunki na strażników albo nazwane predykaty, zanim testy i mutacja "
                "będą w stanie to pokryć."
            ),
            file=method.file,
            line=method.lineno,
            evidence={
                "complexity": method.complexity,
                "threshold": threshold,
                "nloc": method.nloc,
                "snippet": snippet,
            },
        )

    @staticmethod
    def _signature_snippet(change: ChangeContext, method: MethodComplexity) -> str:
        # Pierwsza linia sygnatury, nie całe ciało — fingerprint bez numeru
        # linii przeżyje rebase, snippet całego ciała by go psuł
        # (PLAN-G1-complexity.md §5.2).
        source = change.file_at(change.head_sha, method.file)
        if source is None:
            return method.name
        lines = source.splitlines()
        idx = method.lineno - 1
        if 0 <= idx < len(lines):
            return lines[idx].strip()
        return method.name
