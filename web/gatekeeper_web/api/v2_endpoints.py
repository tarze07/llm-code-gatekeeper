"""Endpointy etapów 2–5: rejestr repozytoriów, kolejka, oceny, polityki.

Osobny moduł, ta sama wersja API (`/api/v1`) — plik `v1.py` opisuje odczyt
raportów i już jest długi, a to jest inny obszar odpowiedzialności.

Reguła obowiązująca w całym module: **API nie przyjmuje ścieżek do wykonania,
poleceń ani nazw pluginów**. Operator wybiera zarejestrowany projekt,
zatwierdzony profil polityki i nazwę wersji Git, którą panel weryfikuje jako
obiekt commit (PLAN-WEB-UI.md §5, §8).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from ..config import Settings
from ..deps import (
    get_policies,
    get_queue,
    get_repository,
    get_reviews,
    get_settings,
    load_run,
    require_job,
    require_project,
)
from ..jobs.spec import JobInputError, build_job_input, describe_input
from ..security import verify_csrf
from ..services import metrics as metrics_service
from ..services import policies as policy_service
from ..services.environment import collect as collect_environment
from ..services.repos import RepoError, list_refs, preview_scope, resolve_repo_path
from ..storage import (
    Job,
    JobQueue,
    PolicyStore,
    PolicyStoreError,
    Project,
    Repository,
    ReviewStore,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])

API_VERSION = "1"

MAX_YAML_CHARS = 256 * 1024


# ------------------------------------------------------- repozytorium i zakres


@router.get("/projects/{project_id}/refs")
def project_refs(
    project_id: int,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Wersje Git, które operator może wybrać. Lista pochodzi z repozytorium."""
    project = require_project(repository, project_id)
    repo = _repo_path(project, settings)
    return {
        "api_version": API_VERSION,
        "refs": [
            {"name": ref.name, "kind": ref.kind, "sha": ref.sha, "subject": ref.subject}
            for ref in list_refs(repo)
        ],
    }


@router.post("/projects/{project_id}/preview", dependencies=[Depends(verify_csrf)])
def project_preview(
    project_id: int,
    payload: dict[str, Any],
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Dokładny zakres zmiany — to samo, co zobaczy silnik."""
    project = require_project(repository, project_id)
    repo = _repo_path(project, settings)
    base = str(payload.get("base") or "")
    head = str(payload.get("head") or "HEAD")
    ticket = payload.get("ticket") or None
    try:
        preview = preview_scope(repo, base, head, ticket=ticket)
    except RepoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"api_version": API_VERSION, "preview": _preview_dict(preview)}


# ----------------------------------------------------------------- środowisko


@router.get("/environment")
def environment(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    env = collect_environment(settings.state_dir)
    return {
        "api_version": API_VERSION,
        "can_run": env.can_run,
        "blockers": env.blockers,
        "isolation": {
            "filesystem": env.isolation_available,
            "network": env.network_isolation,
            "note": env.isolation_note,
        },
        "tools": [
            {
                "name": t.name,
                "purpose": t.purpose,
                "available": t.available,
                "version": t.version,
            }
            for t in env.tools
        ],
        "plugins": [
            {"group": p.group, "label": p.label, "names": list(p.names)} for p in env.plugins
        ],
        "packages": env.packages,
        "gates": env.gates,
        "disk_free_mb": env.disk_free_mb,
    }


# --------------------------------------------------------------------- zadania


@router.post("/jobs", status_code=202, dependencies=[Depends(verify_csrf)])
def create_job(
    request: Request,
    payload: dict[str, Any],
    response: Response,
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Zleca kontrolę. Odpowiada `202 Accepted` — praca dopiero się zacznie."""
    project = require_project(repository, int(payload.get("project_id") or 0))
    job, created = enqueue_check(
        repository,
        policies,
        queue,
        settings,
        project,
        base=str(payload.get("base") or ""),
        head=str(payload.get("head") or "HEAD"),
        ticket=payload.get("ticket") or None,
        gates=tuple(payload.get("gates") or ()) or None,
        fast_path=bool(payload.get("fast_path", True)),
        idempotency_key=payload.get("idempotency_key") or None,
    )
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return JSONResponse(
        status_code=202,
        content={"api_version": API_VERSION, "created": created, "job": _job_dict(job)},
        headers={"Location": f"/api/v1/jobs/{job.id}"},
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: int, queue: JobQueue = Depends(get_queue)) -> dict[str, Any]:
    job = require_job(queue, job_id)
    return {
        "api_version": API_VERSION,
        "job": _job_dict(job),
        "input": describe_input(job.input),
    }


@router.get("/jobs/{job_id}/events")
def job_events(
    job_id: int,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    queue: JobQueue = Depends(get_queue),
) -> dict[str, Any]:
    """Przyrostowy postęp: „co nowego po numerze N"."""
    job = require_job(queue, job_id)
    events = queue.events(job_id, after=after, limit=limit)
    return {
        "api_version": API_VERSION,
        "state": job.state,
        "state_label": job.state_label,
        "terminal": job.is_terminal,
        "run_id": job.run_id,
        "last_seq": events[-1].seq if events else after,
        "events": [
            {
                "seq": e.seq,
                "at": e.at,
                "kind": e.kind,
                "gate": e.gate,
                "message": e.message,
                "completed": e.completed,
                "total": e.total,
            }
            for e in events
        ],
    }


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel_job(job_id: int, queue: JobQueue = Depends(get_queue)) -> dict[str, Any]:
    """Idempotentne żądanie zatrzymania — powtórzenie nie jest błędem."""
    job = require_job(queue, job_id)
    if job.is_terminal:
        return {"api_version": API_VERSION, "job": _job_dict(job), "changed": False}
    return {
        "api_version": API_VERSION,
        "job": _job_dict(queue.request_cancel(job.id)),
        "changed": True,
    }


@router.post("/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(verify_csrf)])
def retry_job(
    job_id: int,
    payload: dict[str, Any] | None = None,
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ponowienie tworzy **nowe** zadanie i nowy przebieg.

    `mode=same_scope` powtarza dokładnie ten sam zamrożony zakres,
    `mode=latest` liczy zakres od nowa dla tych samych nazw Git.
    """
    original = require_job(queue, job_id)
    mode = str((payload or {}).get("mode") or "same_scope")
    if mode not in ("same_scope", "latest"):
        raise HTTPException(status_code=422, detail="`mode` musi być `same_scope` albo `latest`")

    project = require_project(repository, original.project_id)
    if mode == "same_scope":
        job, created = queue.enqueue(project.id, original.input, retry_of=original.id)
    else:
        scope = original.input.get("scope") or {}
        job, created = enqueue_check(
            repository,
            policies,
            queue,
            settings,
            project,
            base=str(scope.get("base_ref") or ""),
            head=str(scope.get("head_ref") or "HEAD"),
            ticket=original.input.get("ticket"),
            gates=tuple(original.input.get("gates") or ()) or None,
            fast_path=bool(original.input.get("fast_path", True)),
            retry_of=original.id,
        )
    return {"api_version": API_VERSION, "created": created, "job": _job_dict(job), "mode": mode}


@router.get("/projects/{project_id}/jobs")
def list_project_jobs(
    project_id: int,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repository: Repository = Depends(get_repository),
    queue: JobQueue = Depends(get_queue),
) -> dict[str, Any]:
    require_project(repository, project_id)
    return {
        "api_version": API_VERSION,
        "total": queue.count_jobs(project_id),
        "jobs": [_job_dict(j) for j in queue.list_jobs(project_id, limit=limit, offset=offset)],
    }


# ------------------------------------------------------------ oceny i incydenty


@router.post(
    "/projects/{project_id}/runs/{run_id}/findings/{fingerprint}/reviews",
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
def create_review(
    project_id: int,
    run_id: str,
    fingerprint: str,
    payload: dict[str, Any],
    repository: Repository = Depends(get_repository),
    reviews: ReviewStore = Depends(get_reviews),
) -> dict[str, Any]:
    """Ocena znaleziska. Nie zmienia decyzji przebiegu i nie tworzy wyjątku."""
    loaded = load_run(repository, project_id, run_id)
    finding = loaded.view.find_finding(fingerprint)
    if finding is None:
        raise HTTPException(
            status_code=404, detail=f"przebieg {run_id} nie zawiera znaleziska {fingerprint!r}"
        )
    verdict = str(payload.get("verdict") or "")
    if verdict not in ("true_positive", "false_positive"):
        raise HTTPException(
            status_code=422,
            detail="`verdict` musi być `true_positive` albo `false_positive`",
        )
    note = _text(payload.get("note"), "note", 2000)
    author = _text(payload.get("author"), "author", 120)
    review = reviews.record(
        project_id=project_id,
        run_id=run_id,
        fingerprint=fingerprint,
        verdict=verdict,  # type: ignore[arg-type]
        rule_id=finding.rule_id,
        gate=finding.gate,
        author=author,
        note=note,
    )
    return {
        "api_version": API_VERSION,
        "review": {
            "id": review.id,
            "verdict": review.verdict,
            "label": review.label,
            "author": review.author,
            "note": review.note,
            "created_at": review.created_at,
        },
        # Powiedziane wprost, żeby nikt nie liczył na cichą zmianę raportu.
        "note": "ocena nie zmienia decyzji przebiegu ani nie tworzy wyjątku od reguły",
    }


@router.post(
    "/projects/{project_id}/runs/{run_id}/incidents",
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
def mark_incident(
    project_id: int,
    run_id: str,
    payload: dict[str, Any] | None = None,
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    """Oznacza przebieg, po którym zmiana wywołała incydent na produkcji."""
    load_run(repository, project_id, run_id)
    note = _text((payload or {}).get("note"), "note", 2000)
    repository.mark_incident(project_id, run_id, note)
    return {"api_version": API_VERSION, "run_id": run_id, "caused_incident": True, "note": note}


@router.get("/metrics")
def metrics(
    request: Request,
    project_id: int | None = Query(None, ge=1),
    days: int = Query(30, ge=1, le=3650),
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    if project_id is not None:
        require_project(repository, project_id)
    report = metrics_service.collect(request.app.state.database, days=days, project_id=project_id)
    return {
        "api_version": API_VERSION,
        "days": report.days,
        "runs": report.runs,
        "notes": report.notes,
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "display": m.display,
                "unit": m.unit,
                "target": m.target,
                "note": m.note,
            }
            for m in report.metrics
        ],
        "rules": [
            {
                "rule_id": r.rule_id,
                "occurrences": r.occurrences,
                "unique_findings": r.unique_findings,
                "judged": r.judged,
                "true_positives": r.true_positives,
                "precision": r.precision,
            }
            for r in report.rules
        ],
    }


# --------------------------------------------------------------------- polityki


@router.get("/policies")
def list_policies(policies: PolicyStore = Depends(get_policies)) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "profiles": [
            {
                "id": profile.id,
                "slug": profile.slug,
                "name": profile.name,
                "active_revision_id": profile.active_revision_id,
                "revisions": [_revision_dict(r) for r in policies.list_revisions(profile.id)],
            }
            for profile in policies.list_profiles()
        ],
    }


@router.post("/policies", status_code=201, dependencies=[Depends(verify_csrf)])
def create_policy_profile(
    payload: dict[str, Any], policies: PolicyStore = Depends(get_policies)
) -> dict[str, Any]:
    name = _text(payload.get("name"), "name", 200)
    if not name:
        raise HTTPException(status_code=422, detail="profil polityki musi mieć nazwę")
    profile = policies.create_profile(name)
    return {"api_version": API_VERSION, "profile": {"id": profile.id, "slug": profile.slug}}


@router.post("/policies/{profile_id}/drafts", status_code=201, dependencies=[Depends(verify_csrf)])
def create_draft(
    profile_id: int, payload: dict[str, Any], policies: PolicyStore = Depends(get_policies)
) -> dict[str, Any]:
    """Szkic powstaje na bazie zatwierdzonej wersji albo z podanej treści."""
    base = policies.active_revision(profile_id)
    policy_yaml = payload.get("policy_yaml")
    if policy_yaml is None and base is not None:
        policy_yaml = base.policy_yaml
    if not isinstance(policy_yaml, str) or not policy_yaml.strip():
        raise HTTPException(status_code=422, detail="szkic wymaga treści polityki")
    exceptions_yaml = payload.get("exceptions_yaml", base.exceptions_yaml if base else None)
    scope_map_yaml = payload.get("scope_map_yaml", base.scope_map_yaml if base else None)
    for name, text in (
        ("policy_yaml", policy_yaml),
        ("exceptions_yaml", exceptions_yaml),
        ("scope_map_yaml", scope_map_yaml),
    ):
        if isinstance(text, str) and len(text) > MAX_YAML_CHARS:
            raise HTTPException(status_code=422, detail=f"{name}: plik jest zbyt duży")
    try:
        revision = policies.create_revision(
            profile_id,
            policy_yaml,
            exceptions_yaml if isinstance(exceptions_yaml, str) else None,
            scope_map_yaml if isinstance(scope_map_yaml, str) else None,
            author=_text(payload.get("author"), "author", 120),
            note=_text(payload.get("note"), "note", 500),
        )
    except PolicyStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"api_version": API_VERSION, "revision": _revision_dict(revision)}


@router.post("/policy-revisions/{revision_id}/validate", dependencies=[Depends(verify_csrf)])
def validate_revision(
    revision_id: int, policies: PolicyStore = Depends(get_policies)
) -> dict[str, Any]:
    """Walidacja tym samym `Policy.load()` + `lint()`, co CLI."""
    revision = policies.get_revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"nie znam wersji polityki {revision_id}")
    with TemporaryDirectory(prefix="gk-policy-") as tmp:
        result = policy_service.validate(revision, Path(tmp))
    active = policies.active_revision(revision.profile_id)
    changes = []
    if active is not None and active.id != revision.id and result.ok:
        with TemporaryDirectory(prefix="gk-policy-prev-") as tmp:
            previous = policy_service.validate(active, Path(tmp))
        changes = [
            {"kind": c.kind, "detail": c.detail, "loosens": c.loosens}
            for c in policy_service.compare(previous.summary, result.summary)
        ]
    return {
        "api_version": API_VERSION,
        "ok": result.ok,
        "errors": result.errors,
        "expiring": result.expiring,
        "expired": result.expired,
        "summary": result.summary,
        "changes": changes,
    }


@router.post("/policy-revisions/{revision_id}/activate", dependencies=[Depends(verify_csrf)])
def activate_revision(
    revision_id: int,
    payload: dict[str, Any] | None = None,
    policies: PolicyStore = Depends(get_policies),
) -> dict[str, Any]:
    """Aktywacja dotyczy przyszłych zadań. Zapisanych raportów nie zmienia."""
    revision = policies.get_revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"nie znam wersji polityki {revision_id}")
    with TemporaryDirectory(prefix="gk-policy-") as tmp:
        result = policy_service.validate(revision, Path(tmp))
    if not result.ok:
        raise HTTPException(
            status_code=422,
            detail="polityka nie przechodzi walidacji: " + "; ".join(result.errors),
        )
    body = payload or {}
    try:
        activated = policies.activate(
            revision_id,
            author=_text(body.get("author"), "author", 120),
            note=_text(body.get("note"), "note", 500),
        )
    except PolicyStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"api_version": API_VERSION, "revision": _revision_dict(activated)}


# ------------------------------------------------------------------ wspólne


def enqueue_check(
    repository: Repository,
    policies: PolicyStore,
    queue: JobQueue,
    settings: Settings,
    project: Project,
    *,
    base: str,
    head: str = "HEAD",
    ticket: str | None = None,
    gates: tuple[str, ...] | None = None,
    fast_path: bool = True,
    idempotency_key: str | None = None,
    retry_of: int | None = None,
) -> tuple[Job, bool]:
    """Zamraża wejście i wkłada zadanie do kolejki. Wspólne dla API i formularza."""
    if project.archived:
        raise HTTPException(status_code=409, detail="projekt jest zarchiwizowany")
    repo = _repo_path(project, settings)
    if project.policy_profile_id is None:
        raise HTTPException(
            status_code=409,
            detail="projekt nie ma przypisanego profilu polityki — panel nie zgaduje "
            "kryteriów oceny",
        )
    revision = policies.active_revision(project.policy_profile_id)
    if revision is None:
        raise HTTPException(
            status_code=409,
            detail="profil polityki nie ma aktywnej wersji — aktywuj ją przed uruchomieniem",
        )
    try:
        preview = preview_scope(repo, base, head, ticket=ticket)
        payload = build_job_input(
            project, revision, preview, gates=gates, fast_path=fast_path, ticket=ticket
        )
    except (RepoError, JobInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return queue.enqueue(
        project.id, payload, idempotency_key=idempotency_key, retry_of=retry_of
    )


def _repo_path(project: Project, settings: Settings) -> Path:
    """Ścieżkę walidujemy **także przy wykonaniu** — katalog mógł się zmienić."""
    if not project.repo_path:
        raise HTTPException(
            status_code=409,
            detail=f"projekt {project.name!r} nie ma zarejestrowanego repozytorium",
        )
    try:
        return resolve_repo_path(project.repo_path, settings.allowed_repo_roots)
    except RepoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _preview_dict(preview: Any) -> dict[str, Any]:
    return {
        "base_ref": preview.base_ref,
        "head_ref": preview.head_ref,
        "base_tip_sha": preview.base_tip_sha,
        "head_sha": preview.head_sha,
        "merge_base": preview.merge_base,
        "uses_merge_base": preview.uses_merge_base,
        "branch": preview.branch,
        "ticket": preview.ticket,
        "total_files": preview.total_files,
        "total_lines": preview.total_lines,
        "effective_files": preview.effective_files,
        "effective_lines": preview.effective_lines,
        "generated_files": preview.generated_files,
        "test_files": preview.test_files,
        "docs_only": preview.docs_only,
        "paths": list(preview.paths),
        "total_commits": preview.total_commits,
        "commits_truncated": preview.commits_truncated,
        "commits": [
            {
                "sha": commit.sha,
                "author": commit.author,
                "date": commit.date,
                "subject": commit.subject,
            }
            for commit in preview.commits
        ],
    }


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "state": job.state,
        "state_label": job.state_label,
        "terminal": job.is_terminal,
        "cancel_requested": job.cancel_requested,
        "run_id": job.run_id,
        "error": job.error,
        "retry_of": job.retry_of,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        # Decyzja polityki istnieje wyłącznie dla zadania z zapisanym raportem.
        "has_result": job.has_result,
    }


def _revision_dict(revision: Any) -> dict[str, Any]:
    return {
        "id": revision.id,
        "profile_id": revision.profile_id,
        "revision": revision.revision,
        "state": revision.state,
        "content_hash": revision.content_hash[:16],
        "author": revision.author,
        "note": revision.note,
        "created_at": revision.created_at,
        "activated_at": revision.activated_at,
    }


def _text(value: Any, name: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"`{name}` musi być tekstem")
    trimmed = value.strip()
    if len(trimmed) > limit:
        raise HTTPException(status_code=422, detail=f"`{name}` przekracza {limit} znaków")
    return trimmed or None
