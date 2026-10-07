"""Redakcja ścieżek tymczasowych i katalogów panelu (POSIX oraz Windows)."""

from __future__ import annotations

import pytest

from gatekeeper_web.services.redaction import redact_text, redact_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("blad w /tmp/gatekeeper-wt-abc/src/a.py", "blad w …/a.py"),
        ("/var/folders/xy/T/gk", "…/gk"),
        ("plik /srv/repo/a.py zostaje", "plik /srv/repo/a.py zostaje"),
    ],
)
def test_posix_bez_zmian(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\Jan Kowalski\AppData\Local\Temp\gatekeeper-wt-abc\src\a.py",
        "C:/Users/jan/AppData/Local/Temp/gatekeeper-wt-abc/src/a.py",
        r"C:\\Users\\jan\\AppData\\Local\\Temp\\gatekeeper-wt-abc\\src\\a.py",
        r"D:\Windows\Temp\gatekeeper-wt-abc\src\a.py",
        r"C:\Users\JAN~1\AppData\Local\Temp\gatekeeper-wt-abc\src\a.py",
    ],
)
def test_windows_temp_skracany_do_nazwy_pliku(raw: str) -> None:
    out = redact_text(f"blad w {raw} linia 3")
    assert out == "blad w …/a.py linia 3"
    assert "Users" not in out and "Windows" not in out


def test_windows_katalog_temp_bez_ogona() -> None:
    assert redact_text(r"C:\Users\jan\AppData\Local\Temp") == "…/Temp"


def test_windows_zwykla_sciezka_zostaje() -> None:
    raw = r"C:\Users\jan\repo\src\a.py"
    assert redact_text(raw) == raw


def test_katalog_stanu_panelu_i_zadania() -> None:
    assert redact_text("/home/jan/.local/state/gatekeeper-web/panel.db") == "…/panel.db"
    assert redact_text("/data/prace/gk-job-42-ab/repo/x.py") == "…/x.py"
    assert redact_text(r"E:\stan\prace\gk-job-42-ab\repo\x.py") == "…/x.py"
    assert (
        redact_text(r"C:\Users\jan\.local\state\gatekeeper-web\prace\gk-job-1\a.py") == "…/a.py"
    )


def test_redakcja_dziala_zagniezdnie_na_json_ucieczkach() -> None:
    data = {"evidence": ["C:\\\\Users\\\\jan\\\\AppData\\\\Local\\\\Temp\\\\wt\\\\a.py"]}
    assert redact_value(data) == {"evidence": ["…/a.py"]}
