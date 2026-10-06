"""API panelu, wersja 1.

Zakres etapu 1: projekty jako pojemnik na raporty, historia, kompletny wynik
i eksport. Endpointy uruchamiające kontrolę (`POST /api/v1/jobs`) świadomie
nie istnieją — panel w tym wydaniu niczego nie wykonuje, a udawanie kolejki
zwracającej 202 byłoby obietnicą bez pokrycia (PLAN-WEB-UI.md §9, etap 3).

API nie przyjmuje ścieżek plików, poleceń ani nazw pluginów. Raport wchodzi
jako treść żądania, a nie jako ścieżka do odczytu przez serwer.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..config import Settings
from ..deps import (
    DEFAULT_PAGE_SIZE,
    clamp_page_size,
    get_policies,
    get_repository,
    get_settings,
    load_run,
    require_project,
)
from ..security import verify_csrf
from ..services import export
from ..services.importing import ImportOutcome, import_report
from ..services.reports import MAX_BYTES, ReportImportError
from ..services.repos import RepoError, resolve_repo_path
from ..storage import (
    PolicyStore,
    Project,
    ReportConflict,
    ReportRow,
    Repository,
    RunFilter,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])

API_VERSION = "1"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

_EXPORT_TYPES = {
    "json": ("application/json; charset=utf-8", "json"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "html": ("text/html; charset=utf-8", "html"),
}


# ------------------------------------------------------------------ projekty


@router.get("/projects")
def list_projects(
    include_archived: bool = Query(False, description="Pokaż też zarchiwizowane"),
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    projects = repository.list_projects(include_archived=include_archived)
    return {"api_version": API_VERSION, "projects": [_project_dict(p) for p in projects]}


@router.post("/projects", status_code=201, dependencies=[Depends(verify_csrf)])
def create_project(
    payload: dict[str, Any],
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="projekt musi mieć nazwę")
    if len(name) > 200:
        raise HTTPException(status_code=422, detail="nazwa projektu jest zbyt długa")
    repo_label = payload.get("repo_label")
    if repo_label is not None and not isinstance(repo_label, str):
        raise HTTPException(status_code=422, detail="`repo_label` musi być tekstem")
    project = repository.create_project(name, (repo_label or None))
    return {"api_version": API_VERSION, "project": _project_dict(project)}


@router.patch("/projects/{project_id}", dependencies=[Depends(verify_csrf)])
def patch_project(
    project_id: int,
    payload: dict[str, Any],
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Nazwa, ścieżka repozytorium, profil polityki, archiwizacja."""
    project = require_project(repository, project_id)

    repo_path: str | None = None
    if payload.get("repo_path"):
        # Ścieżka z formularza jest walidowana tu i **ponownie** przy
        # uruchomieniu: katalog mógł się w międzyczasie zmienić (plan §8).
        try:
            repo_path = str(
                resolve_repo_path(str(payload["repo_path"]), settings.allowed_repo_roots)
            )
        except RepoError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    profile_id: int | None = None
    if payload.get("policy_profile_id") is not None:
        profile_id = int(payload["policy_profile_id"])
        if policies.get_profile(profile_id) is None:
            raise HTTPException(
                status_code=422, detail=f"nie znam profilu polityki o ID {profile_id}"
            )

    name = payload.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise HTTPException(status_code=422, detail="nazwa projektu nie może być pusta")

    refreshed = repository.update_project(
        project.id,
        name=name if isinstance(name, str) else None,
        repo_path=repo_path,
        policy_profile_id=profile_id,
        # Archiwizacja ukrywa projekt, nie kasuje raportów ani repozytorium.
        archived=bool(payload["archived"]) if "archived" in payload else None,
    )
    return {"api_version": API_VERSION, "project": _project_dict(refreshed)}


# ------------------------------------------------------------------ historia


@router.get("/projects/{project_id}/runs")
def list_runs(
    project_id: int,
    verdict: str | None = Query(None, pattern="^(PASS|PASS-WITH-REVIEW|BLOCK)$"),
    since: str | None = Query(None, max_length=40),
    until: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    require_project(repository, project_id)
    flt = RunFilter(
        project_id=project_id,
        verdict=verdict,
        since=since,
        until=until,
        query=q,
        limit=clamp_page_size(limit),
        offset=offset,
    )
    rows = repository.list_reports(flt)
    return {
        "api_version": API_VERSION,
        "total": repository.count_reports(flt),
        "limit": flt.limit,
        "offset": flt.offset,
        "runs": [_row_dict(r) for r in rows],
    }


@router.get("/projects/{project_id}/runs/{run_id}")
def get_run(
    project_id: int,
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    loaded = load_run(repository, project_id, run_id)
    assert loaded.row.payload is not None
    return {
        "api_version": API_VERSION,
        "run": _row_dict(loaded.row),
        # Kompletny zapis, nie skrót: panel nie stosuje limitu 10 znalezisk
        # z komentarza w PR.
        "report": loaded.row.payload,
    }


@router.get("/projects/{project_id}/runs/{run_id}/report")
def get_run_report(
    project_id: int,
    run_id: str,
    format: Literal["json", "markdown", "html"] = Query("json"),
    repository: Repository = Depends(get_repository),
) -> Response:
    loaded = load_run(repository, project_id, run_id)
    assert loaded.row.payload is not None
    if format == "json":
        body = export.report_json(loaded.row.payload)
    elif format == "markdown":
        body = export.report_markdown(loaded.run)
    else:
        body = export.report_html(loaded.view)
    media_type, extension = _EXPORT_TYPES[format]
    filename = _safe_filename(f"gatekeeper-{loaded.row.project_slug}-{run_id}.{extension}")
    return Response(
        content=body,
        media_type=media_type,
        headers={
            # Zawsze jako pobranie: wyeksportowany HTML nie ma się renderować
            # w originie panelu, bo zawiera styl w treści.
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# -------------------------------------------------------------------- import


@router.post("/reports/import", status_code=201, dependencies=[Depends(verify_csrf)])
async def import_json_report(
    request: Request,
    project_id: int = Form(...),
    file: UploadFile | None = File(None),
    repository: Repository = Depends(get_repository),
) -> JSONResponse:
    """Import raportu JSON do wskazanego projektu.

    Projekt wybiera operator. Pole `repo` z raportu jest **danymi**, a nie
    wskazaniem, co uruchomić ani gdzie zapisać (PLAN-WEB-UI.md §5).
    """
    project = require_project(repository, project_id)
    raw, label = await _read_upload(request, file)
    return JSONResponse(status_code=201, content=_import(repository, project, raw, label))


def _import(
    repository: Repository, project: Project, raw: bytes, label: str | None
) -> dict[str, Any]:
    try:
        outcome: ImportOutcome = import_report(repository, project, raw, label)
    except ReportImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "api_version": API_VERSION,
        "created": outcome.created,
        "message": outcome.message,
        "run": _row_dict(outcome.row),
    }


async def _read_upload(request: Request, file: UploadFile | None) -> tuple[bytes, str | None]:
    if file is not None:
        raw = await file.read(MAX_BYTES + 1)
        return raw, (file.filename or None)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="brak pliku raportu w żądaniu")
    return body, None


# --------------------------------------------------------------- pomocnicze


def _project_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "repo_label": project.repo_label,
        "repo_path": project.repo_path,
        "policy_profile_id": project.policy_profile_id,
        "runnable": project.runnable,
        "archived": project.archived,
        "created_at": project.created_at,
    }


def _row_dict(row: ReportRow) -> dict[str, Any]:
    return {
        "project_id": row.project_id,
        "project_slug": row.project_slug,
        "run_id": row.run_id,
        "origin": row.origin,
        "format_version": row.format_version,
        "verdict": row.verdict,
        "started_at": row.started_at,
        "duration_s": row.duration_s,
        "base_sha": row.base_sha,
        "head_sha": row.head_sha,
        "repo": row.repo,
        "policy_version": row.policy_version,
        "gate_count": row.gate_count,
        "gate_error_count": row.gate_error_count,
        "finding_count": row.finding_count,
        "stored_at": row.stored_at,
        "source_label": row.source_label,
        "job_id": row.job_id,
        # Incydent to fakt dopisany po przebiegu, nie zmiana jego decyzji.
        "caused_incident": row.caused_incident,
        "incident_note": row.incident_note,
    }


def _safe_filename(raw: str) -> str:
    return _FILENAME_SAFE.sub("_", raw)[:120]
