"""Aplikacja HTTP panelu.

`create_app()` przyjmuje ustawienia jawnie, żeby test i `gatekeeper-web serve`
budowały dokładnie to samo — testy nie mają prawa dostawać innej aplikacji niż
uruchamiana lokalnie.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, pages, security
from .api import v1_router, v2_router
from .auth import OperatorAuth
from .config import Settings
from .security import token_for
from .storage import Database, JobQueue, PolicyStore, Repository, ReviewStore
from .templating import STATIC_DIR, environment

_env = environment()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_state_dir()

    app = FastAPI(
        title="llm-code-gatekeeper — panel",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        # Schemat OpenAPI zostaje: jest opisem kontraktu API, ale interaktywne
        # „Try it out” w panelu wykonującym narzędzia to zbędna powierzchnia.
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings
    app.state.auth = OperatorAuth.from_settings(settings)
    app.state.database = Database(settings.db_path)
    app.state.repository = Repository(app.state.database)
    app.state.queue = JobQueue(app.state.database)
    app.state.policies = PolicyStore(app.state.database)
    app.state.reviews = ReviewStore(app.state.database)

    security.install(app, settings.allowed_hosts)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(v1_router)
    app.include_router(v2_router)
    app.include_router(pages.router)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return _error_response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in exc.errors()
        )
        return _error_response(request, 422, detail or "niepoprawne dane wejściowe")

    return app


def _error_response(request: Request, status_code: int, detail: str) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=status_code)
    auth: OperatorAuth | None = getattr(request.app.state, "auth", None)
    body = _env.get_template("error.html").render(
        request=request,
        status_code=status_code,
        detail=detail,
        csrf_token=token_for(request),
        zalogowany=bool(auth and auth.require_login and auth.session_ok(request)),
    )
    return HTMLResponse(body, status_code=status_code)


def app_factory() -> FastAPI:  # pragma: no cover - punkt wejścia dla uvicorna
    return create_app()
