"""Testy G1.complexity (dispatcher core-owy, `gatekeeper_core.gates.g1_complexity`)
na żywym eslincie — integracja, nie golden file. Odpowiednik testów
`test_gate_static.py`. Testy pomijane bez `eslint`/`@typescript-eslint/parser`.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g1_complexity import ComplexityGuard

requires_eslint = pytest.mark.skipif(
    shutil.which("eslint") is None, reason="eslint niedostępny (npm i -g eslint)"
)


@requires_eslint
def test_funkcja_powyzej_progu_blokuje(repo):
    repo.checkout("feature", create=True)
    repo.write(
        "src/skomplikowana.ts",
        "export function f(a: boolean, b: boolean, c: boolean, d: boolean, e: boolean,\n"
        "  f2: boolean, g: boolean, h: boolean, i: boolean, j: boolean): string {\n"
        "  if (a) { if (b) { if (c) { if (d) { if (e) { if (f2) { if (g) { if (h) "
        "{ if (i) { if (j) {\n"
        "    return 'ok';\n"
        "  }}}}}}}}}}\n"
        "  return 'brak';\n"
        "}\n",
    )
    repo.commit("feat: zbyt zlozona funkcja")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = ComplexityGuard({}).run(change)

    assert result.status == "fail"
    assert result.facts["complexity.over_threshold_count"] == 1
    assert result.facts["complexity.max"] > 10
    assert result.findings[0].rule_id == "complexity.too_high"


@requires_eslint
def test_prosta_funkcja_przechodzi(repo):
    repo.checkout("feature", create=True)
    repo.write("src/prosta.ts", "export function f(x: number): number {\n  return x + 1;\n}\n")
    repo.commit("feat: prosta funkcja")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = ComplexityGuard({}).run(change)

    assert result.status == "pass"
    assert result.findings == []
