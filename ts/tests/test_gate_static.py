"""Testy G1.static (dispatcher core-owy, `gatekeeper_core.gates.g1_static`)
na żywych binariach tsc/eslint — integracja, nie golden file. Adapter ma już
testy na zapisanym wyjściu (`test_adapters_linters.py`); tu sprawdzamy, że
`TsJsStaticChecker` faktycznie dogfooduje się przez entry points
`gatekeeper.static_checkers` i że filtrowanie do zmienionych linii, decyzja
`pass`/`fail`, obsługa braku narzędzia działają na żywo. Testy pomijane bez tsc.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g1_static import StaticGuard

requires_tsc = pytest.mark.skipif(
    shutil.which("tsc") is None, reason="tsc niedostępny (npm i -g typescript)"
)


@requires_tsc
def test_ts_bez_tsconfig_przechodzi_bez_wolania_tsc(repo):
    repo.checkout("feature", create=True)
    repo.write("app.ts", "export const x: number = 'nie liczba';\n")
    repo.commit("feat: ts bez configu")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["static.ts_files_checked"] == 1
    assert result.facts["static.tsconfig_found"] is False


@requires_tsc
def test_wymyslone_wywolanie_api_w_ts_blokuje(repo):
    repo.checkout("feature", create=True)
    repo.write(
        "tsconfig.json",
        '{"compilerOptions": {"strict": true, "noEmit": true, "target": "ES2020", '
        '"module": "commonjs"}, "include": ["*.ts"]}\n',
    )
    repo.write(
        "app.ts",
        "function add(a: number, b: number): number {\n"
        "  return a + b;\n"
        "}\n\n"
        'add("x", "y");\n',
    )
    repo.commit("feat: ts z halucynowanym wywolaniem")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "fail"
    assert any(f.rule_id == "tsc.TS2345" for f in result.findings)
    assert result.facts["static.tsc_available"] is True


@requires_tsc
def test_eslint_bez_configu_przechodzi_bez_wolania_narzedzia(repo):
    repo.checkout("feature", create=True)
    repo.write("app.js", "eval('cokolwiek');\n")
    repo.commit("feat: js bez configu eslinta")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["static.js_files_checked"] == 1
    assert result.facts["static.eslint_config_found"] is False


@requires_tsc
def test_eslint_z_configem_lapie_reguly_problem(repo):
    repo.checkout("feature", create=True)
    repo.write(
        "eslint.config.js",
        "module.exports = [{ rules: { 'no-eval': 'error' }, "
        "languageOptions: { ecmaVersion: 2020, sourceType: 'script' } }];\n",
    )
    repo.write("app.js", "function run(input) {\n  return eval(input);\n}\n")
    repo.commit("feat: js z eval")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "fail"
    assert any(f.rule_id == "eslint.no-eval" for f in result.findings)
    assert result.facts["static.eslint_available"] is True
