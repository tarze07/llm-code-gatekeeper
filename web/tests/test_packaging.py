"""Zasoby CSS/JS jadą w pakiecie — instalacja panelu nie wymaga Node.js."""

from __future__ import annotations

from importlib.resources import files


def test_arkusz_i_skrypt_sa_w_pakiecie() -> None:
    static = files("gatekeeper_web") / "static"
    css = static.joinpath("app.css")
    js = static.joinpath("app.js")
    assert css.is_file()
    assert js.is_file()
    assert ".znacznik--block" in css.read_text(encoding="utf-8")
    assert "setupFindingFilter" in js.read_text(encoding="utf-8")
    login = files("gatekeeper_web") / "templates" / "login.html"
    assert login.is_file()
