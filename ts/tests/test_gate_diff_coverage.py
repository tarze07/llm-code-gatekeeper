"""Testy G2.diff_coverage na prawdziwym `vitest --coverage` + `diff-cover` —
odpowiednik `test_gate_diff_coverage.py` w python- i csharp-packu.

Sedno: `--branch-coverage` ma łapać gałąź **wykonaną, ale nie sprawdzoną**,
nie tylko linię niewykonaną. Sam licznik linii tego nie odróżnia.
"""

from __future__ import annotations

from conftest import requires_helper
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_diff_coverage import DiffCoverage

APP_Z_GALEZIA = """\
export function add(a: number, b: number): number {
  return a + b;
}

export function classify(n: number): string {
  if (n >= 0) {
    return "non-negative";
  }
  return "negative";
}
"""


def _testy(*bloki: str) -> str:
    return (
        'import { it, expect } from "vitest";\n'
        'import { classify } from "../src/app";\n\n' + "\n".join(bloki)
    )


@requires_helper
def test_pelne_pokrycie_obu_galezi_daje_ratio_rowne_jeden(ts_repo):
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_GALEZIA)
    ts_repo.write(
        "tests/app.test.ts",
        _testy(
            'it("dodatnie", () => {\n  expect(classify(5)).toBe("non-negative");\n});\n\n'
            'it("ujemne", () => {\n  expect(classify(-5)).toBe("negative");\n});\n'
        ),
    )
    ts_repo.commit("feat: classify + oba przypadki")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = DiffCoverage({}).run(change)

    assert result.status == "pass"
    assert result.facts["coverage.diff_ratio"] == 1.0
    assert result.facts["coverage.tool_available"] is True


@requires_helper
def test_niepokryta_galaz_obniza_ratio(ts_repo):
    """Test dotyka tylko gałęzi `if`, nigdy `return "negative"`."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_GALEZIA)
    ts_repo.write(
        "tests/app.test.ts",
        _testy('it("dodatnie", () => {\n  expect(classify(5)).toBe("non-negative");\n});\n'),
    )
    ts_repo.commit("feat: classify + jeden przypadek")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = DiffCoverage({}).run(change)

    ratio = result.facts["coverage.diff_ratio"]
    assert ratio is not None and ratio < 1.0
    assert result.facts["coverage.total_lines"] > result.facts["coverage.covered_lines"]


@requires_helper
def test_zmiana_bez_kodu_produkcyjnego_jest_pomijana(ts_repo):
    ts_repo.checkout("feature", create=True)
    ts_repo.write("README.md", "# dokumentacja\n")
    ts_repo.commit("docs")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = DiffCoverage({}).run(change)

    assert result.status == "skipped"
