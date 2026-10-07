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

from gatekeeper_ts.adapters.linters import TsJsStaticChecker, has_eslint_config

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

    assert result.status == "pass", result.message
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

    assert result.status == "fail", result.message
    assert any(f.rule_id == "tsc.TS2345" for f in result.findings)
    assert result.facts["static.tsc_available"] is True


@requires_tsc
def test_eslint_bez_configu_przechodzi_bez_wolania_narzedzia(repo):
    repo.checkout("feature", create=True)
    repo.write("app.js", "eval('cokolwiek');\n")
    repo.commit("feat: js bez configu eslinta")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "pass", result.message
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

    assert result.status == "fail", result.message
    assert any(f.rule_id == "eslint.no-eval" for f in result.findings)
    assert result.facts["static.eslint_available"] is True


# ------------------------------------------------- brak configu = brak dowodu
# (REVIEW.md §5 P1). Te testy nie potrzebują tsc/eslinta: przy brakującym
# configu checker nie woła narzędzia, więc wynik zależy wyłącznie od polityki.

REQUIRE_ALL = {"require_tsc": True, "require_eslint": True}


def _check(repo, config):
    change = ChangeContext.from_git(repo.path, "main", "HEAD")
    return TsJsStaticChecker().check(change, config, "G1.static", 60.0)


def test_require_tsc_bez_tsconfig_to_error_nie_pass(repo):
    repo.checkout("feature", create=True)
    repo.write("app.ts", "export const x: number = 'nie liczba';\n")
    repo.commit("feat: ts bez configu")

    outcome = _check(repo, {"require_tsc": True})

    assert outcome.error is not None
    assert "tsconfig.json" in outcome.error
    assert outcome.facts["static.tsconfig_found"] is False


def test_require_tsc_honoruje_tsconfig_path(repo):
    repo.checkout("feature", create=True)
    repo.write("app.ts", "export const x = 1;\n")
    repo.commit("feat: ts")

    outcome = _check(repo, {"require_tsc": True, "tsconfig_path": "config/tsconfig.app.json"})

    assert outcome.error is not None
    assert "config/tsconfig.app.json" in outcome.error


def test_require_eslint_bez_configu_to_error_nie_pass(repo):
    repo.checkout("feature", create=True)
    repo.write("app.js", "eval('cokolwiek');\n")
    repo.commit("feat: js bez configu eslinta")

    outcome = _check(repo, {"require_eslint": True})

    assert outcome.error is not None
    assert "eslint" in outcome.error
    assert outcome.facts["static.eslint_config_found"] is False


def test_require_tsc_nie_dotyczy_zmiany_tylko_w_js(repo):
    """tsc sprawdza TypeScript — sam `.js` nie wymaga tsconfiga. Bez configu
    eslinta i bez `require_eslint` checker nie woła żadnego narzędzia."""
    repo.checkout("feature", create=True)
    repo.write("app.js", "module.exports = 1;\n")
    repo.commit("feat: js")

    outcome = _check(repo, {"require_tsc": True})

    assert outcome.error is None, outcome.error
    assert outcome.facts["static.ts_files_checked"] == 0
    assert outcome.facts["static.js_files_checked"] == 1


@pytest.mark.parametrize(
    ("path", "content"),
    [
        (".eslintrc", "{}\n"),
        ("eslint.config.mjs", "export default [];\n"),
        ("package.json", '{"name": "x", "eslintConfig": {"rules": {}}}\n'),
    ],
)
def test_wykrywa_config_eslinta(tmp_path, path, content):
    (tmp_path / path).write_text(content, encoding="utf-8")
    assert has_eslint_config(tmp_path) is True


def test_package_json_bez_eslintconfig_to_nie_config(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}\n', encoding="utf-8")
    assert has_eslint_config(tmp_path) is False
    (tmp_path / "package.json").write_text("{zepsuty", encoding="utf-8")
    assert has_eslint_config(tmp_path) is False


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("app.py", "x = 1\n"),
        ("Program.cs", "class P {}\n"),
        ("docs/notatka.md", "tekst\n"),
    ],
)
def test_zmiana_bez_ts_js_nie_wymaga_configow(repo, path, content):
    """Repo bez TS/JS w diffie nie może dostać `error` za brak tsconfiga."""
    repo.checkout("feature", create=True)
    repo.write(path, content)
    repo.commit("feat: nie-TS")

    outcome = _check(repo, REQUIRE_ALL)

    assert outcome.error is None, outcome.error
    assert outcome.facts["static.ts_files_checked"] == 0
    assert outcome.facts["static.js_files_checked"] == 0
