"""Strony HTML panelu.

Widoki renderuje serwer. JavaScript jest dodatkiem (filtrowanie listy
znalezisk bez przeładowania), a nie warunkiem działania: bez niego strona
nadal pokazuje komplet danych i wszystkie formularze działają.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .api.v2_endpoints import enqueue_check
from .auth import OperatorAuth, safe_next_path
from .config import Settings
from .deps import (
    DEFAULT_PAGE_SIZE,
    clamp_page_size,
    get_policies,
    get_queue,
    get_repository,
    get_reviews,
    get_settings,
    load_run,
    require_job,
    require_project,
)
from .jobs.spec import describe_input
from .security import token_for, verify_csrf
from .services import metrics as metrics_service
from .services import policies as policy_service
from .services.environment import cached as cached_environment
from .services.environment import collect as collect_environment
from .services.importing import ImportOutcome, import_report
from .services.reports import MAX_BYTES, ReportImportError
from .services.repos import RepoError, list_refs, preview_scope, resolve_repo_path
from .storage import (
    JobQueue,
    PolicyStore,
    ReportConflict,
    Repository,
    ReviewStore,
    RunFilter,
)
from .templating import environment

router = APIRouter(include_in_schema=False)
_env = environment()

VERDICTS = ("BLOCK", "PASS-WITH-REVIEW", "PASS")

def render(request: Request, template: str, status_code: int = 200, **context: Any) -> Response:
    auth: OperatorAuth | None = getattr(request.app.state, "auth", None)
    body = _env.get_template(template).render(
        request=request,
        csrf_token=token_for(request),
        verdicts=VERDICTS,
        zalogowany=bool(auth and auth.require_login and auth.session_ok(request)),
        **context,
    )
    return HTMLResponse(body, status_code=status_code)


# ---------------------------------------------------------------- logowanie


@router.get("/logowanie")
def login_page(
    request: Request,
    nastepny: str = Query(""),
) -> Response:
    auth: OperatorAuth = request.app.state.auth
    target = safe_next_path(nastepny)
    if not auth.require_login or auth.session_ok(request):
        return RedirectResponse(target, status_code=303)
    return render(request, "login.html", nastepny=target, blad="")


@router.post("/logowanie", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    code: str = Form(""),
    nastepny: str = Form(""),
) -> Response:
    auth: OperatorAuth = request.app.state.auth
    target = safe_next_path(nastepny)
    if not auth.require_login or auth.session_ok(request):
        return RedirectResponse(target, status_code=303)
    if not auth.check_and_consume(code):
        return render(
            request,
            "login.html",
            status_code=403,
            blad="Niepoprawny albo już zużyty kod startowy. Nowy kod jest w terminalu serwera.",
            nastepny=target,
        )
    response = RedirectResponse(target, status_code=303)
    auth.attach_session(request, response)
    return response


@router.post("/wyloguj", dependencies=[Depends(verify_csrf)])
def logout(request: Request) -> Response:
    auth: OperatorAuth = request.app.state.auth
    response = RedirectResponse("/logowanie", status_code=303)
    auth.clear_session(response)
    return response


# -------------------------------------------------------------------- pulpit


@router.get("/")
def dashboard(
    request: Request,
    repository: Repository = Depends(get_repository),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> Response:
    from .jobs.supervisor import supervisor_running
    from .storage import ACTIVE_STATES

    env = cached_environment(settings.state_dir)
    return render(
        request,
        "dashboard.html",
        projects=repository.list_projects(),
        recent=repository.list_reports(RunFilter(limit=10)),
        counts=repository.verdict_counts(),
        total=repository.count_reports(RunFilter(limit=1)),
        audit=repository.audit_tail(8),
        aktywne=queue.list_jobs(states=ACTIVE_STATES, limit=10),
        supervisor_active=supervisor_running(settings),
        blockers=env.blockers,
    )


# ------------------------------------------------------------------ projekty


@router.get("/projekty")
def projects_page(
    request: Request,
    repository: Repository = Depends(get_repository),
    blad: str | None = Query(None, max_length=300),
) -> Response:
    projects = repository.list_projects(include_archived=True)
    return render(
        request,
        "projects.html",
        projects=projects,
        counts={p.id: repository.count_reports(RunFilter(project_id=p.id)) for p in projects},
        blad=blad,
    )


@router.post("/projekty", dependencies=[Depends(verify_csrf)])
def create_project_form(
    name: str = Form(..., max_length=200),
    repo_label: str = Form("", max_length=500),
    repository: Repository = Depends(get_repository),
) -> Response:
    if not name.strip():
        return RedirectResponse("/projekty?blad=Projekt+musi+mieć+nazwę.", status_code=303)
    repository.create_project(name, repo_label.strip() or None)
    return RedirectResponse("/projekty", status_code=303)


@router.post("/projekty/{project_id}/archiwum", dependencies=[Depends(verify_csrf)])
def archive_project(
    project_id: int,
    archived: str = Form("1"),
    repository: Repository = Depends(get_repository),
) -> Response:
    project = require_project(repository, project_id)
    repository.set_archived(project.id, archived == "1")
    return RedirectResponse("/projekty", status_code=303)


# ------------------------------------------------------------------ historia


@router.get("/przebiegi")
def runs_page(
    request: Request,
    projekt: str | None = Query(None, max_length=20),
    decyzja: str | None = Query(None, max_length=40),
    od: str | None = Query(None, max_length=40),
    do: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=200),
    strona: int = Query(1, ge=1),
    na_stronie: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    repository: Repository = Depends(get_repository),
) -> Response:
    project_id = None
    if projekt:
        try:
            project_id = int(projekt)
        except ValueError as exc:
            raise HTTPException(422, "projekt: oczekiwano numeru projektu") from exc
        if not 0 < project_id < 2**63:
            raise HTTPException(422, "projekt: numer projektu poza zakresem")
    limit = clamp_page_size(na_stronie)
    flt = RunFilter(
        project_id=project_id,
        verdict=decyzja if decyzja in VERDICTS else None,
        since=od or None,
        until=(do + "T23:59:59") if do else None,
        query=q,
        limit=limit,
        offset=(strona - 1) * limit,
    )
    total = repository.count_reports(flt)
    return render(
        request,
        "runs.html",
        rows=repository.list_reports(flt),
        projects=repository.list_projects(include_archived=True),
        flt=flt,
        filters={"projekt": project_id, "decyzja": decyzja, "od": od, "do": do, "q": q},
        total=total,
        page=strona,
        per_page=limit,
        pages=max(1, (total + limit - 1) // limit),
    )


# ------------------------------------------------------------ jeden przebieg


@router.get("/projekty/{project_id}/przebiegi/{run_id}")
def run_page(
    request: Request,
    project_id: int,
    run_id: str,
    zaimportowano: int | None = Query(None),
    duplikat: int | None = Query(None),
    repository: Repository = Depends(get_repository),
    reviews: ReviewStore = Depends(get_reviews),
) -> Response:
    loaded = load_run(repository, project_id, run_id)
    komunikat = None
    if zaimportowano:
        komunikat = "Raport zaimportowany."
    elif duplikat:
        komunikat = "Ten raport był już zaimportowany — panel nie utworzył duplikatu."
    return render(
        request,
        "run.html",
        run=loaded.view,
        row=loaded.row,
        komunikat=komunikat,
        oceny=reviews.latest_for_project(project_id),
    )


@router.get("/projekty/{project_id}/przebiegi/{run_id}/znaleziska/{fingerprint}")
def finding_page(
    request: Request,
    project_id: int,
    run_id: str,
    fingerprint: str,
    repository: Repository = Depends(get_repository),
    reviews: ReviewStore = Depends(get_reviews),
) -> Response:
    loaded = load_run(repository, project_id, run_id)
    finding = loaded.view.find_finding(fingerprint)
    if finding is None:
        raise HTTPException(
            status_code=404, detail=f"przebieg {run_id} nie zawiera znaleziska {fingerprint!r}"
        )
    gate = next((g for g in loaded.view.gates if g.gate == finding.gate), None)
    return render(
        request,
        "finding.html",
        run=loaded.view,
        finding=finding,
        gate=gate,
        # Ocena jest przypisana do pary projekt + fingerprint, nie do przebiegu.
        ocena=reviews.latest(project_id, fingerprint),
        historia=reviews.history(project_id, fingerprint),
    )


# -------------------------------------------------------------------- import


@router.get("/import")
def import_page(
    request: Request, repository: Repository = Depends(get_repository)
) -> Response:
    return render(
        request,
        "import.html",
        projects=repository.list_projects(),
        max_kb=MAX_BYTES // 1024,
        blad=None,
    )


@router.post("/import", dependencies=[Depends(verify_csrf)])
async def import_form(
    request: Request,
    project_id: int = Form(...),
    file: UploadFile = File(...),
    repository: Repository = Depends(get_repository),
) -> Response:
    project = require_project(repository, project_id)
    raw = await file.read(MAX_BYTES + 1)
    try:
        outcome: ImportOutcome = import_report(repository, project, raw, file.filename)
    except (ReportImportError, ReportConflict) as exc:
        status = 409 if isinstance(exc, ReportConflict) else 422
        return render(
            request,
            "import.html",
            status_code=status,
            projects=repository.list_projects(),
            max_kb=MAX_BYTES // 1024,
            blad=str(exc),
        )
    flag = "zaimportowano=1" if outcome.created else "duplikat=1"
    return RedirectResponse(
        f"/projekty/{project.id}/przebiegi/{outcome.row.run_id}?{flag}", status_code=303
    )


# --------------------------------------------------------------- środowisko


@router.get("/srodowisko")
def environment_page(
    request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    """Czym panel dysponuje. Brak Bubblewrapa jest tu powiedziany wprost."""
    return render(request, "environment.html", env=collect_environment(settings.state_dir))


# ------------------------------------------------------------ jeden projekt


@router.get("/projekty/{project_id}")
def project_page(
    request: Request,
    project_id: int,
    blad: str | None = Query(None, max_length=400),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> Response:
    project = require_project(repository, project_id)
    refs: list[Any] = []
    refs_error: str | None = None
    if project.repo_path:
        try:
            refs = list_refs(resolve_repo_path(project.repo_path, settings.allowed_repo_roots))
        except RepoError as exc:
            refs_error = str(exc)
    profile = (
        policies.get_profile(project.policy_profile_id)
        if project.policy_profile_id is not None
        else None
    )
    active = policies.active_revision(profile.id) if profile else None
    return render(
        request,
        "project.html",
        project=project,
        profiles=policies.list_profiles(),
        profile=profile,
        active_revision=active,
        refs=refs[:50],
        refs_error=refs_error,
        jobs=queue.list_jobs(project.id, limit=10),
        runs=repository.list_reports(RunFilter(project_id=project.id, limit=10)),
        allowed_roots=[str(root) for root in settings.allowed_repo_roots],
        blad=blad,
    )


@router.post("/projekty/{project_id}/ustawienia", dependencies=[Depends(verify_csrf)])
def update_project_form(
    project_id: int,
    name: str = Form(..., max_length=200),
    repo_path: str = Form("", max_length=4096),
    policy_profile_id: str = Form(""),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    settings: Settings = Depends(get_settings),
) -> Response:
    project = require_project(repository, project_id)
    resolved: str | None = None
    if repo_path.strip():
        try:
            resolved = str(resolve_repo_path(repo_path, settings.allowed_repo_roots))
        except RepoError as exc:
            return RedirectResponse(
                f"/projekty/{project.id}?blad={quote(str(exc))}", status_code=303
            )
    profile_id: int | None = None
    if policy_profile_id.strip():
        profile_id = int(policy_profile_id)
        if policies.get_profile(profile_id) is None:
            return RedirectResponse(
                f"/projekty/{project.id}?blad=Nie+znam+takiego+profilu.", status_code=303
            )
    repository.update_project(
        project.id, name=name, repo_path=resolved, policy_profile_id=profile_id
    )
    return RedirectResponse(f"/projekty/{project.id}", status_code=303)


# ------------------------------------------------------------ nowa kontrola


@router.get("/nowa-kontrola")
def new_check_page(
    request: Request,
    projekt: int | None = Query(None, ge=1),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    settings: Settings = Depends(get_settings),
) -> Response:
    projects = [p for p in repository.list_projects() if p.runnable]
    project = (
        require_project(repository, projekt) if projekt else (projects[0] if projects else None)
    )
    refs: list[Any] = []
    refs_error: str | None = None
    if project and project.repo_path:
        try:
            refs = list_refs(resolve_repo_path(project.repo_path, settings.allowed_repo_roots))
        except RepoError as exc:
            refs_error = str(exc)
    env = cached_environment(settings.state_dir)
    return render(
        request,
        "new_check.html",
        projects=projects,
        project=project,
        refs=refs[:100],
        refs_error=refs_error,
        gates=[g["id"] for g in env.gates],
        blockers=env.blockers,
        preview=None,
        form={},
        idempotency_key="",
        blad=None,
    )


@router.post("/nowa-kontrola/podglad", dependencies=[Depends(verify_csrf)])
def preview_check(
    request: Request,
    project_id: int = Form(...),
    base: str = Form(..., max_length=200),
    head: str = Form("HEAD", max_length=200),
    ticket: str = Form("", max_length=64),
    fast_path: str = Form(""),
    gate: list[str] | None = Form(None),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Podgląd dokładnego zakresu **przed** uruchomieniem czegokolwiek."""
    project = require_project(repository, project_id)
    env = cached_environment(settings.state_dir)
    form = {
        "project_id": project.id,
        "base": base,
        "head": head,
        "ticket": ticket,
        "fast_path": bool(fast_path),
        "gates": gate or [],
    }
    preview = None
    blad = None
    try:
        repo = resolve_repo_path(project.repo_path or "", settings.allowed_repo_roots)
        preview = preview_scope(repo, base, head, ticket=ticket or None)
    except RepoError as exc:
        blad = str(exc)

    profile = (
        policies.get_profile(project.policy_profile_id)
        if project.policy_profile_id is not None
        else None
    )
    active = policies.active_revision(profile.id) if profile else None
    if active is None:
        blad = blad or "profil polityki nie ma aktywnej wersji — aktywuj ją przed uruchomieniem"

    return render(
        request,
        "new_check.html",
        projects=[p for p in repository.list_projects() if p.runnable],
        project=project,
        refs=[],
        refs_error=None,
        gates=[g["id"] for g in env.gates],
        blockers=env.blockers,
        preview=preview,
        active_revision=active,
        form=form,
        # Klucz powstaje raz, przy podglądzie: podwójne kliknięcie „Uruchom"
        # trafia w to samo zadanie (PLAN-WEB-UI.md §9, scenariusz 6).
        idempotency_key=secrets.token_urlsafe(16),
        blad=blad,
    )


@router.post("/nowa-kontrola", dependencies=[Depends(verify_csrf)])
def start_check(
    project_id: int = Form(...),
    base: str = Form(..., max_length=200),
    head: str = Form("HEAD", max_length=200),
    ticket: str = Form("", max_length=64),
    fast_path: str = Form(""),
    gate: list[str] | None = Form(None),
    idempotency_key: str = Form("", max_length=64),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> Response:
    project = require_project(repository, project_id)
    job, _created = enqueue_check(
        repository,
        policies,
        queue,
        settings,
        project,
        base=base,
        head=head,
        ticket=ticket or None,
        gates=tuple(gate) if gate else None,
        fast_path=bool(fast_path),
        idempotency_key=idempotency_key or None,
    )
    return RedirectResponse(f"/zadania/{job.id}", status_code=303)


# -------------------------------------------------------------------- zadania


@router.get("/zadania")
def jobs_page(
    request: Request,
    projekt: int | None = Query(None, ge=1),
    repository: Repository = Depends(get_repository),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> Response:
    from .jobs.supervisor import supervisor_running

    return render(
        request,
        "jobs.html",
        jobs=queue.list_jobs(projekt, limit=50),
        projects=repository.list_projects(include_archived=True),
        wybrany=projekt,
        supervisor_active=supervisor_running(settings),
    )


@router.get("/zadania/{job_id}")
def job_page(
    request: Request,
    job_id: int,
    repository: Repository = Depends(get_repository),
    queue: JobQueue = Depends(get_queue),
) -> Response:
    job = require_job(queue, job_id)
    project = repository.get_project(job.project_id)
    return render(
        request,
        "job.html",
        job=job,
        project=project,
        opis=describe_input(job.input),
        events=queue.events(job.id, limit=500),
    )


@router.post("/zadania/{job_id}/anuluj", dependencies=[Depends(verify_csrf)])
def cancel_job_form(job_id: int, queue: JobQueue = Depends(get_queue)) -> Response:
    job = require_job(queue, job_id)
    if not job.is_terminal:
        queue.request_cancel(job.id)
    return RedirectResponse(f"/zadania/{job.id}", status_code=303)


@router.post("/zadania/{job_id}/ponow", dependencies=[Depends(verify_csrf)])
def retry_job_form(
    job_id: int,
    mode: str = Form("same_scope"),
    repository: Repository = Depends(get_repository),
    policies: PolicyStore = Depends(get_policies),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> Response:
    original = require_job(queue, job_id)
    project = require_project(repository, original.project_id)
    if mode == "latest":
        scope = original.input.get("scope") or {}
        job, _ = enqueue_check(
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
    else:
        job, _ = queue.enqueue(project.id, original.input, retry_of=original.id)
    return RedirectResponse(f"/zadania/{job.id}", status_code=303)


# --------------------------------------------------------- oceny i incydenty


@router.post(
    "/projekty/{project_id}/przebiegi/{run_id}/znaleziska/{fingerprint}/ocena",
    dependencies=[Depends(verify_csrf)],
)
def review_finding_form(
    project_id: int,
    run_id: str,
    fingerprint: str,
    verdict: str = Form(...),
    author: str = Form("", max_length=120),
    note: str = Form("", max_length=2000),
    repository: Repository = Depends(get_repository),
    reviews: ReviewStore = Depends(get_reviews),
) -> Response:
    loaded = load_run(repository, project_id, run_id)
    finding = loaded.view.find_finding(fingerprint)
    if finding is None:
        raise HTTPException(status_code=404, detail="to znalezisko nie należy do tego przebiegu")
    if verdict not in ("true_positive", "false_positive"):
        raise HTTPException(status_code=422, detail="nieznana ocena")
    reviews.record(
        project_id=project_id,
        run_id=run_id,
        fingerprint=fingerprint,
        verdict=verdict,  # type: ignore[arg-type]
        rule_id=finding.rule_id,
        gate=finding.gate,
        author=author.strip() or None,
        note=note.strip() or None,
    )
    return RedirectResponse(
        f"/projekty/{project_id}/przebiegi/{run_id}/znaleziska/{fingerprint}", status_code=303
    )


@router.post(
    "/projekty/{project_id}/przebiegi/{run_id}/incydent", dependencies=[Depends(verify_csrf)]
)
def mark_incident_form(
    project_id: int,
    run_id: str,
    note: str = Form("", max_length=2000),
    repository: Repository = Depends(get_repository),
) -> Response:
    load_run(repository, project_id, run_id)
    repository.mark_incident(project_id, run_id, note.strip() or None)
    return RedirectResponse(f"/projekty/{project_id}/przebiegi/{run_id}", status_code=303)


# -------------------------------------------------------------------- metryki


@router.get("/metryki")
def metrics_page(
    request: Request,
    projekt: int | None = Query(None, ge=1),
    dni: int = Query(30, ge=1, le=3650),
    repository: Repository = Depends(get_repository),
) -> Response:
    if projekt is not None:
        require_project(repository, projekt)
    report = metrics_service.collect(request.app.state.database, days=dni, project_id=projekt)
    return render(
        request,
        "metrics.html",
        report=report,
        projects=repository.list_projects(include_archived=True),
        wybrany=projekt,
        dni=dni,
    )


# ------------------------------------------------------------------- polityki


@router.get("/polityki")
def policies_page(
    request: Request, policies: PolicyStore = Depends(get_policies)
) -> Response:
    profiles = policies.list_profiles()
    return render(
        request,
        "policies.html",
        profiles=[
            {
                "profile": profile,
                "revisions": policies.list_revisions(profile.id),
                "active": policies.active_revision(profile.id),
            }
            for profile in profiles
        ],
    )


@router.post("/polityki", dependencies=[Depends(verify_csrf)])
def create_profile_form(
    name: str = Form(..., max_length=200), policies: PolicyStore = Depends(get_policies)
) -> Response:
    profile = policies.create_profile(name)
    return RedirectResponse(f"/polityki/{profile.id}", status_code=303)


@router.get("/polityki/{profile_id}")
def policy_profile_page(
    request: Request,
    profile_id: int,
    policies: PolicyStore = Depends(get_policies),
) -> Response:
    profile = policies.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"nie znam profilu polityki {profile_id}")
    active = policies.active_revision(profile_id)
    summary = None
    if active is not None:
        with TemporaryDirectory(prefix="gk-policy-") as tmp:
            summary = policy_service.validate(active, Path(tmp)).summary
    return render(
        request,
        "policy_profile.html",
        profile=profile,
        revisions=policies.list_revisions(profile_id),
        active=active,
        summary=summary,
        draft_source=active.policy_yaml if active else "version: 1\n",
        draft_exceptions=(active.exceptions_yaml if active else "") or "",
        draft_scope_map=(active.scope_map_yaml if active else "") or "",
    )


@router.post("/polityki/{profile_id}/szkice", dependencies=[Depends(verify_csrf)])
def create_draft_form(
    profile_id: int,
    policy_yaml: str = Form(...),
    exceptions_yaml: str = Form(""),
    scope_map_yaml: str = Form(""),
    author: str = Form("", max_length=120),
    note: str = Form("", max_length=500),
    policies: PolicyStore = Depends(get_policies),
) -> Response:
    revision = policies.create_revision(
        profile_id,
        policy_yaml,
        exceptions_yaml.strip() or None,
        scope_map_yaml.strip() or None,
        author=author.strip() or None,
        note=note.strip() or None,
    )
    return RedirectResponse(f"/polityki/wersje/{revision.id}", status_code=303)


@router.get("/polityki/wersje/{revision_id}")
def policy_revision_page(
    request: Request, revision_id: int, policies: PolicyStore = Depends(get_policies)
) -> Response:
    """Walidacja i porównanie z wersją aktywną — przed jakąkolwiek aktywacją."""
    revision = policies.get_revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"nie znam wersji polityki {revision_id}")
    with TemporaryDirectory(prefix="gk-policy-") as tmp:
        result = policy_service.validate(revision, Path(tmp))
    active = policies.active_revision(revision.profile_id)
    changes: list[Any] = []
    if active is not None and active.id != revision.id and result.ok:
        with TemporaryDirectory(prefix="gk-policy-prev-") as tmp:
            previous = policy_service.validate(active, Path(tmp))
        changes = policy_service.compare(previous.summary, result.summary)
    return render(
        request,
        "policy_revision.html",
        revision=revision,
        profile=policies.get_profile(revision.profile_id),
        result=result,
        active=active,
        changes=changes,
    )


@router.post("/polityki/wersje/{revision_id}/aktywuj", dependencies=[Depends(verify_csrf)])
def activate_revision_form(
    revision_id: int,
    author: str = Form("", max_length=120),
    note: str = Form("", max_length=500),
    policies: PolicyStore = Depends(get_policies),
) -> Response:
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
    policies.activate(revision_id, author=author.strip() or None, note=note.strip() or None)
    return RedirectResponse(f"/polityki/{revision.profile_id}", status_code=303)
