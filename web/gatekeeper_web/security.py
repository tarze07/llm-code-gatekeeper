"""Granice zaufania HTTP panelu.

Panel czyta raporty z cudzych repozytoriów i uruchamia na nich narzędzia,
więc ochrona nie jest dodatkiem po wystawieniu serwera, tylko częścią
funkcji (PLAN-WEB-UI.md §8). W tym wydaniu:

* nasłuch na pętli zwrotnej i **weryfikacja nagłówka `Host`** — bez niej
  „tylko localhost” przewraca się na DNS rebindingu;
* sesja operatora zakładana jednorazowym kodem startowym; sekret sesji
  nie trafia do URL raportu ani logów;
* CSP bez CDN-a i bez `unsafe-inline`, `nosniff`, brak referrera;
* CSRF metodą double-submit dla każdej metody zapisującej, plus sprawdzenie
  `Origin`, bo import raportu jest operacją zapisu.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .auth import OperatorAuth, is_public_path, login_url

CSRF_COOKIE = "gk_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "style-src 'self'; "
    "script-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # Panel nie jest przeznaczony do indeksowania ani do cache'owania raportów
    # w proxy — treść bywa wrażliwa.
    "Cache-Control": "no-store",
}


def host_allowed(host_header: str | None, allowed: tuple[str, ...]) -> bool:
    if not host_header:
        return False
    host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else (
        host_header.split("]")[0] + "]"
    )
    return host in allowed


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """Brak `Origin` jest dozwolony (zwykłe wysłanie formularza w części
    przeglądarek), obcy `Origin` — nie."""
    if not origin or origin == "null":
        return origin is None
    if not host_header:
        return False
    return origin.split("://", 1)[-1] == host_header


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def token_for(request: Request) -> str:
    """Token z ciasteczka albo świeży — spójny w obrębie jednego żądania."""
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing
    token = getattr(request.state, "csrf_token", None)
    if not token:
        token = issue_token()
        request.state.csrf_token = token
    return str(token)


def attach_token(request: Request, response: Response) -> None:
    if request.cookies.get(CSRF_COOKIE):
        return
    token = token_for(request)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
        max_age=60 * 60 * 12,
    )


async def verify_csrf(request: Request) -> None:
    """Zależność FastAPI dla metod zapisujących."""
    if request.method in SAFE_METHODS:
        return
    if not origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(status_code=403, detail="żądanie z obcego origin")
    cookie = request.cookies.get(CSRF_COOKIE)
    sent = request.headers.get(CSRF_HEADER)
    if sent is None and "form" in (request.headers.get("content-type") or ""):
        form = await request.form()
        raw = form.get(CSRF_FIELD)
        sent = raw if isinstance(raw, str) else None
    if not cookie or not sent or not hmac.compare_digest(cookie, sent):
        raise HTTPException(
            status_code=403,
            detail="brak albo niezgodny token CSRF — odśwież stronę i spróbuj ponownie",
        )


def _unauth_response(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "wymagane logowanie kodem startowym"},
            status_code=401,
            headers=SECURITY_HEADERS,
        )
    return RedirectResponse(login_url(request), status_code=303, headers=SECURITY_HEADERS)


def install(app: FastAPI, allowed_hosts: tuple[str, ...]) -> None:
    """Rejestruje middleware nagłówków, kontroli `Host` i sesji operatora."""

    @app.middleware("http")
    async def _guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not host_allowed(request.headers.get("host"), allowed_hosts):
            return Response(
                "Nieznany nagłówek Host. Panel odpowiada wyłącznie na adresy lokalne.",
                status_code=421,
                media_type="text/plain; charset=utf-8",
                headers=SECURITY_HEADERS,
            )
        auth: OperatorAuth | None = getattr(request.app.state, "auth", None)
        if (
            auth is not None
            and auth.require_login
            and not is_public_path(request.url.path)
            and not auth.session_ok(request)
        ):
            return _unauth_response(request)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        attach_token(request, response)
        return response
