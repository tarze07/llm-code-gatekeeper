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
    # `same-origin`, nie `no-referrer` — i to nie jest kosmetyka.
    #
    # Fetch (§„append a request `Origin` header") każe przeglądarce przy metodzie
    # innej niż GET/HEAD *zserializować Origin jako `null`*, gdy polityka
    # referrera dokumentu to `no-referrer`. Panel wysyłał ten nagłówek przy
    # każdej odpowiedzi, więc każdy jego własny formularz wracał z
    # `Origin: null`, a kontrola originu odrzucała go jako obcy: 403 przy
    # zakładaniu projektu, profilu polityki i każdym innym zapisie z HTML-a.
    # API działało, bo klient podaje `Origin` jawnie — i dlatego testy
    # na `TestClient` tego nie łapały.
    #
    # `same-origin` trzyma to, po co ten nagłówek tu był: ścieżka raportu nie
    # wycieka do obcej strony. Wewnątrz panelu referrer zostaje, a `Origin`
    # przychodzi prawdziwy, więc kontrola ma co sprawdzać.
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    # Panel nie jest przeznaczony do indeksowania ani do cache'owania raportów
    # w proxy — treść bywa wrażliwa.
    "Cache-Control": "no-store",
}


#: Polityki referrera, przy których przeglądarka **ukrywa nadawcę zapisu**:
#: wysyła `Origin: null` przy każdej metodzie innej niż GET/HEAD
#: (Fetch, „append a request `Origin` header"). Panel odrzuca `null`, więc
#: ustawienie którejkolwiek z nich wyłącza jego własne formularze — i nie widać
#: tego w testach na `TestClient`, bo ten podaje `Origin` jawnie.
REFERRER_POLICIES_HIDING_ORIGIN = frozenset({"no-referrer", "no-referrer-when-downgrade"})


def host_allowed(host_header: str | None, allowed: tuple[str, ...]) -> bool:
    if not host_header:
        return False
    host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else (
        host_header.split("]")[0] + "]"
    )
    return host in allowed


#: Port domyślny schematu — `Origin` pomija `:80`/`:443`, nagłówek `Host` też,
#: więc porównanie gołych napisów wywracało się na samym zapisie adresu.
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _authority(value: str, default_port: str) -> tuple[str, str]:
    """Rozbija `host[:port]` na nazwę i port. IPv6 zostaje w nawiasach."""
    if value.startswith("["):
        name, _, rest = value.partition("]")
        return (name + "]").lower(), (rest[1:] if rest.startswith(":") else default_port)
    name, separator, port = value.rpartition(":")
    if not separator or not port.isdigit():
        return value.lower(), default_port
    return name.lower(), port


def origin_allowed(
    origin: str | None, host_header: str | None, allowed_hosts: tuple[str, ...] = ()
) -> bool:
    """Czy zapis przyszedł z panelu, a nie z cudzej strony.

    Brak `Origin` jest dozwolony — część przeglądarek nie wysyła go przy zwykłym
    formularzu. `null` (piaskownica, `file://`) i obcy adres — nie.

    Porównujemy rozbity adres, nie napis. `localhost:8080` i `127.0.0.1:8080`
    to ten sam panel: oba są na liście dozwolonych hostów, oba wskazują ten
    jeden proces nasłuchujący na tym porcie. Wymóg identycznego napisu odrzucał
    operatora za to, że w pasku adresu ma inny zapis pętli zwrotnej niż
    przeglądarka wysłała w `Host`.

    Ochrona zostaje: **port musi się zgadzać**, więc inna usługa na tej samej
    maszynie (`localhost:3000`) nadal jest obcym originem, a cudza domena
    nie pasuje do żadnego wpisu.
    """
    if origin is None:
        return True
    if not origin or origin == "null":
        return False
    if not host_header:
        return False

    scheme, separator, authority = origin.partition("://")
    if not separator or not authority:
        return False
    default_port = _DEFAULT_PORTS.get(scheme.lower(), "")
    origin_name, origin_port = _authority(authority, default_port)
    host_name, host_port = _authority(host_header, default_port)

    if origin_port != host_port:
        return False
    if origin_name == host_name:
        return True
    known = {name.lower() for name in allowed_hosts}
    return origin_name in known and host_name in known


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


#: Nagłówki pochodzą od klienta, więc do komunikatu idzie krótki wycinek.
#: Szablon błędu escapuje, ale strona błędu nie jest miejscem na cudzy adres
#: w pełnej okazałości.
MAX_ECHO = 80


def _settings_hosts(request: Request) -> tuple[str, ...]:
    settings = getattr(request.app.state, "settings", None)
    hosts = getattr(settings, "allowed_hosts", ())
    return tuple(hosts)


async def verify_csrf(request: Request) -> None:
    """Zależność FastAPI dla metod zapisujących."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin_allowed(origin, host, _settings_hosts(request)):
        # Sam komunikat „żądanie z obcego origin" nie mówił operatorowi nic:
        # nie da się z niego zgadnąć, czy panel jest za proxy, czy adres
        # w pasku różni się portem. Nazywamy oba nagłówki.
        oczekiwany = (host or "brak")[:MAX_ECHO]
        if origin == "null":
            # Nieprzezroczysty origin: strona z piaskownicy, `file://` albo
            # dokument, którego polityka referrera każe przeglądarce ukryć
            # nadawcę. Przepuścić tego nie wolno — tak samo wygląda formularz
            # ze strony https, która strzela w panel po http.
            raise HTTPException(
                status_code=403,
                detail=(
                    "żądanie bez rozpoznawalnego origin (Origin: null) — panel "
                    f"odpowiada na {oczekiwany}. Tak wygląda zapis ze strony "
                    "w piaskownicy, z pliku `file://` albo zza proxy. Otwórz panel "
                    "bezpośrednio pod adresem, na którym nasłuchuje."
                ),
            )
        widziany = (origin or "brak")[:MAX_ECHO]
        raise HTTPException(
            status_code=403,
            detail=(
                f"żądanie z obcego origin: przeglądarka podała Origin {widziany}, "
                f"a panel odpowiada na {oczekiwany}. Zapisy przyjmuje wyłącznie "
                "ze swojego adresu — otwórz panel pod tym samym adresem i portem, "
                "na których nasłuchuje, bez proxy po drodze."
            ),
        )
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
