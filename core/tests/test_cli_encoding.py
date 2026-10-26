"""Konsola Windows: CLI musi wypisywać UTF-8, nie krzaki z cp1252."""

from __future__ import annotations

import io
import sys

import pytest

from gatekeeper_core import cli


def _strumien() -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", errors="strict")


def test_win32_przestawia_strumienie_na_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_out, out = _strumien()
    raw_err, err = _strumien()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    cli._utf8_streams()
    print("OK — 4 reguł blokujących")
    out.flush()

    assert out.encoding == "utf-8"
    assert raw_out.getvalue().decode("utf-8").startswith("OK — 4 reguł blokujących")
    assert err.encoding == "utf-8"


def test_inne_systemy_nie_dotykaja_strumieni(monkeypatch: pytest.MonkeyPatch) -> None:
    _, out = _strumien()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdout", out)

    cli._utf8_streams()

    assert out.encoding == "cp1252"


def test_win32_pomija_strumien_bez_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    cli._utf8_streams()  # nie rzuca
