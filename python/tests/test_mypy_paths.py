"""parse_mypy sprowadza ścieżki do postaci repo-względnej posix — inaczej
znalezisko nie pasuje do zmienionego pliku i wypada po cichu z filtra diffa."""

from __future__ import annotations

from pathlib import Path

from gatekeeper_python.adapters.linters import parse_mypy


def _line(file: str) -> str:
    return (
        '{"file": ' + __import__("json").dumps(file) + ', "line": 3, "message": "zły typ", '
        '"code": "arg-type", "severity": "error"}\n'
    )


def test_mypy_backslash_wzgledny():
    findings = parse_mypy(_line("src\\a.py"), Path("/repo"), "G1.static")
    assert findings[0].file == "src/a.py"


def test_mypy_sciezka_bezwzgledna_i_alias_work():
    assert parse_mypy(_line("/work/src/a.py"), Path("/home/u/repo"), "G1.static")[0].file == (
        "src/a.py"
    )


def test_mypy_sciezka_bezwzgledna_w_repo(tmp_path):
    findings = parse_mypy(_line(str(tmp_path / "pkg" / "a.py")), tmp_path, "G1.static")
    assert findings[0].file == "pkg/a.py"
