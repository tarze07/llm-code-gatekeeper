"""Sesja operatora zakładana jednorazowym kodem startowym.

PLAN-WEB-UI.md §8: sekret sesji nie trafia do URL raportu ani logów.
Kod jest wypisywany raz w terminalu `serve` i zużywany przy pierwszym
udanym logowaniu — kolejna wizyta wymaga ważnego ciasteczka, nie kodu.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.datastructures import URL

from .config import Settings

SESSION_COOKIE = "gk_session"
SESSION_MAX_AGE = 60 * 60 * 12

PUBLIC_PATHS = frozenset({"/logowanie", "/wyloguj"})


def generate_bootstrap_code() -> str:
    raw = secrets.token_hex(6)
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def generate_session_secret() -> str:
    return secrets.token_urlsafe(32)


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.strip().lower() if ch.isalnum())


def hash_bootstrap_code(code: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        normalize_code(code).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def safe_next_path(value: str | None) -> str:
    """Tylko ścieżka względna w obrębie panelu — bez otwartego przekierowania."""
    if not value:
        return "/"
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if "\\" in candidate or "\n" in candidate or "\r" in candidate:
        return "/"
    if "://" in candidate:
        return "/"
    return candidate


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/static/")


@dataclass
class OperatorAuth:
    require_login: bool
    session_secret: str
    bootstrap_hash: str | None = None
    _consumed: bool = field(default=False, init=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> OperatorAuth:
        if not settings.require_login:
            return cls(require_login=False, session_secret="", bootstrap_hash=None)
        secret = settings.session_secret or generate_session_secret()
        digest = settings.bootstrap_hash
        if not digest and settings.bootstrap_code:
            digest = hash_bootstrap_code(settings.bootstrap_code, secret)
        if not digest:
            digest = hash_bootstrap_code(generate_bootstrap_code(), secret)
        return cls(require_login=True, session_secret=secret, bootstrap_hash=digest)

    def session_ok(self, request: Request) -> bool:
        if not self.require_login:
            return True
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            return False
        return self._cookie_valid(cookie)

    def check_and_consume(self, code: str) -> bool:
        if not self.require_login:
            return True
        if self._consumed or not self.bootstrap_hash:
            return False
        digest = hash_bootstrap_code(code, self.session_secret)
        if not hmac.compare_digest(digest, self.bootstrap_hash):
            return False
        self._consumed = True
        self.bootstrap_hash = None
        return True

    def attach_session(self, request: Request, response: Response) -> str:
        nonce = secrets.token_urlsafe(24)
        token = f"{nonce}.{self._sign(nonce)}"
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
            max_age=SESSION_MAX_AGE,
        )
        return token

    def clear_session(self, response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE, path="/")

    def _sign(self, nonce: str) -> str:
        return hmac.new(
            self.session_secret.encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _cookie_valid(self, cookie: str) -> bool:
        nonce, sep, signature = cookie.partition(".")
        if not sep or not nonce or not signature:
            return False
        expected = self._sign(nonce)
        return hmac.compare_digest(signature, expected)


def login_url(request: Request) -> str:
    target = URL("/logowanie")
    path = request.url.path
    if path and path != "/" and not is_public_path(path):
        target = target.include_query_params(nastepny=path)
    return str(target)
