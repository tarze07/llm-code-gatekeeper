"""Testy G2.cross_verify (dispatcher core-owy) na prawdziwym vitest —
integracja, nie golden file. Odpowiednik `test_gate_crossverify.py`
w python- i csharp-packu.

`npm install` jest jeden na sesję (fixture `node_modules` w `conftest.py`);
bez npm/sieci testy skipują, nie czerwienią.
"""

from __future__ import annotations

import json

import pytest
from conftest import requires_helper
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_crossverify import CrossVerify

APP_Z_CLASSIFY = """\
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
    return 'import { describe, it, expect } from "vitest";\n' + (
        'import { add, classify } from "../src/app";\n\n' + "\n".join(bloki)
    )


@requires_helper
def test_dobry_test_pada_na_starym_kodzie_i_bramka_przechodzi(ts_repo):
    """Test nowej funkcjonalności *musi* polec na kodzie sprzed zmiany."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_CLASSIFY)
    ts_repo.write(
        "tests/app.test.ts",
        _testy(
            'it("klasyfikuje dodatnie", () => {\n'
            '  expect(classify(5)).toBe("non-negative");\n});\n'
        ),
    )
    ts_repo.commit("feat: classify")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "pass"
    assert result.facts["tests.pass_on_pre_change_code"] is False
    assert result.facts["tests.proved"] == 1
    assert result.findings == []


@requires_helper
def test_bezwartosciowy_test_jest_wykrywany(ts_repo):
    """Test przechodzący identycznie na starym i nowym kodzie nie dowodzi
    niczego o zmianie — bramka musi to zablokować."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_CLASSIFY)
    ts_repo.write(
        "tests/app.test.ts",
        _testy('it("dodaje", () => {\n  expect(add(2, 2)).toBe(4);\n});\n'),
    )
    ts_repo.commit("feat: classify + test nie o tym")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "fail"
    assert result.facts["tests.pass_on_pre_change_code"] is True
    assert result.facts["tests.passing_on_old_code"] == ["tests/app.test.ts::dodaje"]
    assert [f.rule_id for f in result.findings] == ["tests.pass_on_pre_change_code"]


@requires_helper
def test_dwa_testy_rozliczane_osobno_w_jednym_pliku(ts_repo):
    """Uruchamiamy całe pliki, nie pojedyncze testy (patrz docstring
    `testing/runner.py`) — korelacja po nodeid musi mimo to rozdzielić
    wynik testu dowodzącego od wyniku atrapy w tym samym pliku."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_CLASSIFY)
    ts_repo.write(
        "tests/app.test.ts",
        _testy(
            'describe("classify", () => {\n'
            '  it("dodatnie", () => {\n    expect(classify(5)).toBe("non-negative");\n  });\n'
            "});\n\n"
            'describe("add", () => {\n'
            '  it("dodaje", () => {\n    expect(add(2, 2)).toBe(4);\n  });\n'
            "});\n"
        ),
    )
    ts_repo.commit("feat: classify + dwa testy")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "fail"
    assert result.facts["tests.proved"] == 1
    assert result.facts["tests.passing_on_old_code"] == [
        "tests/app.test.ts::add > dodaje"
    ]


@requires_helper
def test_marker_zwalnia_test_z_dowodu(ts_repo):
    """Test charakteryzujący istniejące zachowanie *powinien* przejść na
    starym kodzie — deklaracja autora ma go wyłączyć z blokowania, ale
    zostać policzona."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_CLASSIFY)
    ts_repo.write(
        "tests/app.test.ts",
        _testy(
            "// gatekeeper: characterization\n"
            'it("opisuje istniejace dodawanie", () => {\n'
            "  expect(add(2, 2)).toBe(4);\n});\n"
        ),
    )
    ts_repo.commit("test: charakteryzacja")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "pass"
    assert result.facts["tests.characterization_used"] == 1
    assert result.facts["tests.checked"] == 0
    assert [f.rule_id for f in result.findings] == ["tests.characterization_declared"]


@requires_helper
def test_zmiana_bez_nowych_testow_jest_pomijana(ts_repo):
    """`G2.cross_verify` nie jest bramką „wymagaj testów" — to zadanie
    `G2.diff_coverage`. Bez nowych testów nie ma czego dowodzić."""
    ts_repo.checkout("feature", create=True)
    ts_repo.write("src/app.ts", APP_Z_CLASSIFY)
    ts_repo.commit("feat: classify bez testow")
    change = ChangeContext.from_git(ts_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "skipped"


def test_dowiazanie_workspace_w_node_modules_przerywa_bramke(repo):
    """Odpowiednik kontroli `pip install -e .` w python-packu: gdy
    `node_modules` prowadzi z powrotem do źródeł repo, testy na kopii kodu
    bazowego zaimportowałyby **nowy** kod. To ma być `error`, nie zielony
    wynik bez wartości.

    Ten scenariusz nie wymaga zainstalowanego vitesta — kontrola izolacji
    wykonuje się przed uruchomieniem runnera.
    """
    repo.write("package.json", json.dumps({"devDependencies": {"vitest": "^3"}}))
    repo.write("packages/lib/index.ts", "export const x = 1;\n")
    repo.commit("baza z workspace")
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP_Z_CLASSIFY)
    repo.write(
        "tests/app.test.ts",
        _testy('it("cokolwiek", () => {\n  expect(classify(1)).toBe("non-negative");\n});\n'),
    )
    repo.commit("feat: classify")

    node_modules = repo.path / "node_modules"
    (node_modules / "@app").mkdir(parents=True)
    (node_modules / "@app" / "lib").symlink_to(
        repo.path / "packages" / "lib", target_is_directory=True
    )

    change = ChangeContext.from_git(repo.path, "main", "HEAD")
    result = CrossVerify({}).run(change)

    assert result.status == "error"
    assert result.facts["tests.isolation_broken"] is True
    assert "packages/lib" in result.message


def test_repo_bez_obslugiwanego_runnera_konczy_sie_bledem(repo):
    """Fail-closed: repo na mocha nie dostaje cichego `pass`."""
    repo.write("package.json", json.dumps({"devDependencies": {"mocha": "^10"}}))
    repo.commit("baza na mocha")
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP_Z_CLASSIFY)
    repo.write(
        "tests/app.test.ts",
        _testy('it("cokolwiek", () => {\n  expect(classify(1)).toBe("non-negative");\n});\n'),
    )
    repo.commit("feat: classify")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "error"
    assert "vitest" in result.message and "jest" in result.message


@pytest.mark.parametrize("skip", [True, False])
def test_polityka_moze_swiadomie_wylaczyc_kontrole_izolacji(repo, skip):
    """`skip_isolation_check` to świadoma rezygnacja z dowodu, nie obejście
    błędu — musi działać, ale tylko wtedy, gdy polityka o to prosi."""
    repo.write("package.json", json.dumps({"devDependencies": {"vitest": "^3"}}))
    repo.write("packages/lib/index.ts", "export const x = 1;\n")
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("src/app.ts", APP_Z_CLASSIFY)
    repo.write(
        "tests/app.test.ts",
        _testy('it("cokolwiek", () => {\n  expect(classify(1)).toBe("non-negative");\n});\n'),
    )
    repo.commit("feat")
    node_modules = repo.path / "node_modules"
    node_modules.mkdir()
    (node_modules / "lib").symlink_to(repo.path / "packages" / "lib", target_is_directory=True)

    change = ChangeContext.from_git(repo.path, "main", "HEAD")
    result = CrossVerify({"skip_isolation_check": skip}).run(change)

    # Z wyłączoną kontrolą bramka idzie dalej i wywraca się dopiero na braku
    # vitesta w `node_modules/.bin` — istotne jest, że to **inny** błąd.
    assert result.facts["tests.isolation_broken"] is not skip
