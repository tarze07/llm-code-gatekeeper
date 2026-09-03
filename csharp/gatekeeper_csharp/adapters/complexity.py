"""Złożoność cyklomatyczna (McCabe) dla C# przez `gatekeeper-cs-helper
complexity` — trzecia komenda tego samego helpera Roslyn co
`testing/discovery.py`/`testing/quality.py` (PLAN-G1-complexity.md §8
w core-repo: "ten sam helper Roslyn co PLAN-G2.md — nie budować drugiego
parsera C#"). Algorytm i tabela ΔM: docstring `tools/gatekeeper-cs-helper/
Complexity.cs`.
"""

from __future__ import annotations

from typing import Any

from gatekeeper_core.adapters.base import relative_to_repo
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.plugins import ComplexityOutcome, MethodComplexity

from ..testing.discovery import HelperUnavailable, run_helper


class CsharpComplexityAnalyzer:
    """`ComplexityAnalyzer` (`gatekeeper_core.core.plugins`) dla C#."""

    analyzer_id = "csharp"
    languages = ("csharp",)

    def empty_facts(self) -> dict[str, Any]:
        return {"complexity.csharp_files_checked": 0}

    def analyze(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> ComplexityOutcome:
        include_tests = bool(config.get("include_tests", False))
        files = [
            f.path
            for f in change.effective_files
            if f.language == "csharp"
            and f.status != "D"
            and (include_tests or not change.is_test_file(f.path))
        ]
        facts = self.empty_facts()
        facts["complexity.csharp_files_checked"] = len(files)
        if not files:
            return ComplexityOutcome(methods=[], facts=facts)

        try:
            payload = run_helper("complexity", change.repo, files)
        except HelperUnavailable as exc:
            return ComplexityOutcome(methods=[], facts=facts, error=str(exc))

        methods: list[MethodComplexity] = []
        for raw in payload.get("methods", []) or []:
            methods.append(
                MethodComplexity(
                    file=relative_to_repo(raw["file"], change.repo),
                    name=raw["name"],
                    lineno=raw["lineno"],
                    end_lineno=raw["end_lineno"],
                    complexity=raw["complexity"],
                    nloc=raw.get("nloc", 0),
                )
            )
        return ComplexityOutcome(methods=methods, facts=facts)
