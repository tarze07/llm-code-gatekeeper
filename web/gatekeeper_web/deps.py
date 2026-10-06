"""Zależności współdzielone przez API i strony HTML."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from gatekeeper_core.core.finding import RunResult

from .config import Settings
from .services.deserialize import run_result_from_payload
from .services.view import RunView, build_run_view
from .storage import (
    Job,
    JobQueue,
    PolicyStore,
    Project,
    ReportRow,
    Repository,
    ReviewStore,
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


@dataclass(frozen=True)
class LoadedRun:
    row: ReportRow
    run: RunResult
    view: RunView


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_repository(request: Request) -> Repository:
    repository: Repository = request.app.state.repository
    return repository


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


def get_policies(request: Request) -> PolicyStore:
    policies: PolicyStore = request.app.state.policies
    return policies


def get_reviews(request: Request) -> ReviewStore:
    reviews: ReviewStore = request.app.state.reviews
    return reviews


def require_job(queue: JobQueue, job_id: int) -> Job:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"nie znam zadania o ID {job_id}")
    return job


def require_project(repository: Repository, project_id: int) -> Project:
    project = repository.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie znam projektu o ID {project_id}")
    return project


def load_run(repository: Repository, project_id: int, run_id: str) -> LoadedRun:
    project = require_project(repository, project_id)
    row = repository.get_report(project_id, run_id)
    if row is None or row.payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"projekt {project.name!r} nie ma zapisanego przebiegu {run_id!r}",
        )
    run = run_result_from_payload(row.payload)
    view = build_run_view(
        run,
        project_id=project.id,
        project_slug=project.slug,
        project_name=project.name,
        origin=row.origin,
    )
    return LoadedRun(row=row, run=run, view=view)


def clamp_page_size(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))
