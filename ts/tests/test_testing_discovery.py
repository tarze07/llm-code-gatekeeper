"""Testy `testing/discovery.py` — helper w Node uruchamiany naprawdę, nie
zamockowany: kontrakt między Pythonem a `helper.cjs` (kształt JSON-a, nodeid,
`body_hash`, markery) jest właśnie tym, co ma się nie rozjechać.
"""

from __future__ import annotations

from pathlib import Path

from conftest import requires_helper

from gatekeeper_ts.testing.discovery import TestItem, changed_tests, discover_tests

SUITE = """\
import { describe, it, test, expect } from "vitest";
import { classify } from "../src/app";

describe("classify", () => {
  it("zwraca non-negative", () => {
    expect(classify(5)).toBe("non-negative");
  });

  // gatekeeper: characterization
  it("opisuje stan zastany", () => {
    expect(classify(-1)).toBe("negative");
  });
});

test("na najwyzszym poziomie", () => {
  expect(classify(0)).toBe("non-negative");
});
"""


def _write(root: Path, rel: str, content: str) -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return rel


@requires_helper
def test_nodeid_sklada_sciezke_i_lancuch_describe(tmp_path):
    """`nodeid` musi być tym samym stringiem, który `runner.py` odtwarza
    z `ancestorTitles` + `title` — inaczej korelacja wyniku przebiegu
    z wykrytym testem po cichu przestaje działać."""
    rel = _write(tmp_path, "tests/app.test.ts", SUITE)

    items = discover_tests(tmp_path, [rel])

    assert [i.nodeid for i in items] == [
        "tests/app.test.ts::classify > zwraca non-negative",
        "tests/app.test.ts::classify > opisuje stan zastany",
        "tests/app.test.ts::na najwyzszym poziomie",
    ]
    assert [i.suite for i in items] == ["classify", "classify", None]


@requires_helper
def test_marker_w_komentarzu_zwalnia_test_z_dowodu(tmp_path):
    rel = _write(tmp_path, "tests/app.test.ts", SUITE)

    items = discover_tests(tmp_path, [rel])

    declared = {i.name: i.declared_escape for i in items}
    assert declared["opisuje stan zastany"] == "characterization"
    assert declared["zwraca non-negative"] is None


@requires_helper
def test_body_hash_ignoruje_formatowanie_ale_nie_tresc(tmp_path):
    """Przeformatowanie testu nie czyni z niego nowego — to samo kryterium,
    co `ast.dump(include_attributes=False)` w python-packu."""
    original = _write(
        tmp_path, "a.test.ts", 'it("x", () => {\n  expect(1).toBe(2);\n});\n'
    )
    reformatted = _write(
        tmp_path, "b.test.ts", 'it( "x" ,\n  () => {\n\n    expect( 1 ).toBe( 2 );\n\n  } );\n'
    )
    changed = _write(tmp_path, "c.test.ts", 'it("x", () => {\n  expect(1).toBe(3);\n});\n')

    [a], [b], [c] = (discover_tests(tmp_path, [p]) for p in (original, reformatted, changed))

    assert a.body_hash == b.body_hash
    assert a.body_hash != c.body_hash


@requires_helper
def test_zdjecie_markera_liczy_sie_jako_zmiana_testu(tmp_path):
    """Marker wchodzi do `body_hash`: gdyby nie wchodził, dopisanie
    `// gatekeeper: characterization` do istniejącego testu przeszłoby jako
    „test niezmieniony" i wypadłoby z weryfikacji bez śladu."""
    plain = _write(tmp_path, "a.test.ts", 'it("x", () => {\n  expect(1).toBe(2);\n});\n')
    marked = _write(
        tmp_path,
        "b.test.ts",
        '// gatekeeper: characterization\nit("x", () => {\n  expect(1).toBe(2);\n});\n',
    )

    [a], [b] = (discover_tests(tmp_path, [p]) for p in (plain, marked))

    assert a.body_hash != b.body_hash


@requires_helper
def test_plik_z_bledem_skladni_nie_wywraca_helpera(tmp_path):
    """PR w trakcie edycji nie ma prawa wywrócić bramki — odpowiednik
    `except SyntaxError` w python-packu."""
    broken = _write(tmp_path, "broken.test.ts", 'it("x", () => { expect(')
    ok = _write(tmp_path, "ok.test.ts", 'it("y", () => { expect(1).toBe(1); });')

    items = discover_tests(tmp_path, [broken, ok])

    assert [i.name for i in items] == ["y"]


@requires_helper
def test_rzutowanie_typu_w_ts_nie_jest_czytane_jako_jsx(tmp_path):
    """`<T>expr` w `.ts` to rzutowanie; gdyby helper włączał `jsx` dla `.ts`,
    plik w ogóle by się nie sparsował i test zniknąłby po cichu."""
    rel = _write(
        tmp_path,
        "cast.test.ts",
        'it("rzutuje", () => {\n  const x = <string>(<unknown>"a");\n'
        '  expect(x).toBe("a");\n});\n',
    )

    assert [i.name for i in discover_tests(tmp_path, [rel])] == ["rzutuje"]


@requires_helper
def test_tsx_parsuje_sie_z_wlaczonym_jsx(tmp_path):
    rel = _write(
        tmp_path,
        "comp.test.tsx",
        'it("renderuje", () => {\n  const el = <div className="a" />;\n'
        "  expect(el).toBeDefined();\n});\n",
    )

    assert [i.name for i in discover_tests(tmp_path, [rel])] == ["renderuje"]


def _item(nodeid: str, body_hash: str) -> TestItem:
    return TestItem(
        file=nodeid.split("::")[0],
        name=nodeid.split("::")[-1],
        suite=None,
        nodeid=nodeid,
        lineno=1,
        body_hash=body_hash,
        declared_escape=None,
    )


def test_changed_tests_zwraca_nowe_i_zmodyfikowane():
    base = [_item("a.test.ts::x", "h1"), _item("a.test.ts::y", "h2")]
    head = [
        _item("a.test.ts::x", "h1"),  # bez zmian
        _item("a.test.ts::y", "INNY"),  # zmodyfikowany
        _item("a.test.ts::z", "h3"),  # nowy
    ]

    assert [i.nodeid for i in changed_tests(base, head)] == ["a.test.ts::y", "a.test.ts::z"]
