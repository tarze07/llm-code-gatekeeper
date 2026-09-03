"""Testy `adapters/complexity.py` — parser komunikatu `complexity` eslinta
i dopasowanie nawiasów klamrowych. Czyste funkcje, bez eslinta/gita."""

from __future__ import annotations

import json

from gatekeeper_ts.adapters.complexity import _find_end_lineno, _parse_eslint_complexity


def test_parsowanie_named_function_i_method_i_arrow():
    payload = json.dumps(
        [
            {
                "filePath": "/repo/src/app.ts",
                "source": "function raty() {}\nclass X { metoda() {} }\nconst a = () => 1;\n",
                "messages": [
                    {
                        "ruleId": "complexity",
                        "message": "Function 'raty' has a complexity of 5. Maximum allowed is 1.",
                        "line": 1,
                    },
                    {
                        "ruleId": "complexity",
                        "message": "Method 'metoda' has a complexity of 3. Maximum allowed is 1.",
                        "line": 2,
                    },
                    {
                        "ruleId": "complexity",
                        "message": "Arrow function has a complexity of 2. Maximum allowed is 1.",
                        "line": 3,
                    },
                    {
                        "ruleId": "no-unused-vars",
                        "message": "coś innego, nie complexity",
                        "line": 1,
                    },
                ],
            }
        ]
    )
    result = _parse_eslint_complexity(payload)
    assert len(result) == 1
    file_path, methods = result[0]
    assert file_path == "/repo/src/app.ts"
    assert len(methods) == 3  # reguła no-unused-vars pominięta

    by_name = {m.name: m for m in methods}
    assert by_name["raty"].complexity == 5
    assert by_name["metoda"].complexity == 3
    assert by_name["Arrow function"].complexity == 2  # brak nazwy -> opis reguły jako name


def test_pusty_payload_daje_pusta_liste():
    assert _parse_eslint_complexity("") == []
    assert _parse_eslint_complexity("[]") == []


def test_znajduje_koniec_funkcji_po_zbalansowanych_nawiasach():
    lines = [
        "function f() {",
        "  if (true) {",
        "    return 1;",
        "  }",
        "}",
        "function g() {}",
    ]
    assert _find_end_lineno(lines, 1) == 5


def test_ignoruje_nawiasy_wewnatrz_stringow():
    lines = [
        "function f() {",
        "  const s = '{ nie licz mnie }';",
        "  return s;",
        "}",
    ]
    assert _find_end_lineno(lines, 1) == 4


def test_arrow_expression_bez_nawiasow_daje_lineno_jako_end_lineno():
    lines = ["const a = (x) => x ? 1 : 2;"]
    assert _find_end_lineno(lines, 1) == 1
