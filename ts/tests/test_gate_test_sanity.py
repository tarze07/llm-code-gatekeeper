"""Testy G2.test_sanity (dispatcher core-owy) na testach TS/JS.

Ta bramka nie uruchamia runnera — parsuje tylko drzewo składniowe, więc
nie potrzebuje `node_modules` ani sieci, tylko helpera.
"""

from __future__ import annotations

from conftest import requires_helper
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_test_sanity import TestSanity

APP = "export function classify(n: number): string {\n  return n >= 0 ? 'nn' : 'n';\n}\n"


@requires_helper
def test_atrapa_bez_asercji_jest_blokowana(repo):
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP)
    repo.write(
        "tests/app.test.ts",
        'import { classify } from "../src/app";\n\n'
        'it("wola kod i nic nie sprawdza", () => {\n  classify(5);\n});\n',
    )
    repo.commit("test: atrapa")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = TestSanity({}).run(change)

    assert result.status == "fail"
    assert result.facts["sanity.rule_ids"] == ["test.no_assertion"]
    [finding] = result.findings
    assert finding.file == "tests/app.test.ts"


@requires_helper
def test_porzadny_test_przechodzi(repo):
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP)
    repo.write(
        "tests/app.test.ts",
        'import { classify } from "../src/app";\n\n'
        'it("sprawdza wynik", () => {\n  expect(classify(5)).toBe("nn");\n});\n',
    )
    repo.commit("test: porzadny")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = TestSanity({}).run(change)

    assert result.status == "pass"
    assert result.facts["sanity.checked_count"] == 1
    assert result.findings == []


@requires_helper
def test_bramka_widzi_tylko_testy_z_diffa(repo):
    """Stary dług testowy repo to osobna sprawa — ta bramka odpowiada za to,
    co dokłada ta konkretna zmiana."""
    repo.write("tests/stary.test.ts", 'it("stara atrapa", () => {\n  const x = 1;\n});\n')
    repo.commit("baza z atrapa")
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP)
    repo.write(
        "tests/nowy.test.ts",
        'import { classify } from "../src/app";\n\n'
        'it("nowy porzadny", () => {\n  expect(classify(5)).toBe("nn");\n});\n',
    )
    repo.commit("feat + nowy test")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = TestSanity({}).run(change)

    assert result.status == "pass"
    assert result.facts["sanity.checked_count"] == 1
