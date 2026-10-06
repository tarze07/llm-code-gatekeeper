"""Proces jednego przebiegu.

Uruchamiany przez nadzorcę jako osobny program (`python -m
gatekeeper_web.jobs.worker`), z argumentami jako listą i bez powłoki.
To tutaj — i tylko tutaj — wywołuje się `run_gates()`.

Kolejność zapisu jest istotna: **najpierw utrwalamy raport, potem oznaczamy
zadanie jako zakończone**. Zadanie „zakończone" bez raportu byłoby zielonym
znacznikiem bez dowodu; raport bez zadania da się odzyskać przy starcie
nadzorcy (PLAN-WEB-UI.md §5).
"""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import tempfile
import traceback
from pathlib import Path
from types import FrameType
from typing import Any

from gatekeeper_core.core.progress import ProgressEvent, RunCancelled, RunControl
from gatekeeper_core.core.report import render_json
from gatekeeper_core.core.service import PreparationError, RunRequest, execute, prepare

from ..config import Settings
from ..services.policies import materialize
from ..services.reports import parse_report
from ..storage import Database, Job, JobQueue, PolicyRevision, Repository
from .spec import JobInputError, require_supported

#: Ustawiane przez obsługę SIGTERM. Nadzorca prosi grzecznie, zanim zabije.
_stop_requested = False


def _handle_sigterm(signum: int, frame: FrameType | None) -> None:  # pragma: no cover
    global _stop_requested
    _stop_requested = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wykonanie jednego zadania panelu")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    settings = Settings(state_dir=Path(args.state_dir))
    return run_job(settings, args.job_id)


def run_job(settings: Settings, job_id: int) -> int:
    database = Database(settings.db_path)
    queue = JobQueue(database)
    repository = Repository(database)

    job = queue.get(job_id)
    if job is None:
        print(f"nie znam zadania {job_id}", file=sys.stderr)
        return 2
    if job.state not in ("preparing", "running", "cancelling"):
        # Nadzorca już to zadanie rozliczył — worker nie wskrzesza zadań.
        print(f"zadanie {job_id} jest w stanie {job.state}", file=sys.stderr)
        return 0

    workspace = Path(tempfile.mkdtemp(prefix=f"gk-job-{job_id}-", dir=settings.work_dir))
    try:
        return _execute(settings, queue, repository, job, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _execute(
    settings: Settings,
    queue: JobQueue,
    repository: Repository,
    job: Job,
    workspace: Path,
) -> int:
    payload: dict[str, Any] = job.input
    try:
        require_supported(payload)
        request = _build_request(payload, workspace)
    except (JobInputError, KeyError, TypeError) as exc:
        queue.transition(
            job.id, "failed", ("preparing", "running", "cancelling"),
            error=f"niepoprawne wejście zadania: {exc}",
            message="wejście zadania odrzucone",
        )
        return 1

    control = RunControl(
        observer=lambda event: _record(queue, job.id, event),
        cancelled=lambda: _stop_requested or queue.is_cancel_requested(job.id),
        poll_interval_s=1.0,
        min_cancel_interval_s=1.0,
    )

    try:
        prepared = prepare(request)
    except PreparationError as exc:
        queue.transition(
            job.id, "failed", ("preparing", "running", "cancelling"),
            error=str(exc), message=f"nie da się rozpocząć: {exc}",
        )
        return 1

    queue.append_event(
        job.id,
        "prepared",
        message=(
            f"{len(prepared.gates)} kontroli, zakres "
            f"{prepared.change.base_sha[:12]} → {prepared.change.head_sha[:12]}"
        ),
    )
    if not queue.transition(job.id, "running", ("preparing",), message="analiza wystartowała"):
        # Ktoś zdążył anulować w trakcie przygotowania.
        queue.transition(job.id, "cancelled", ("cancelling",), message="anulowane przed startem")
        return 0

    try:
        result = execute(prepared, control=control)
    except RunCancelled:
        queue.transition(
            job.id,
            "cancelled",
            ("running", "cancelling", "preparing"),
            message="przebieg zatrzymany — wynik nie powstał",
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — awaria przebiegu to stan zadania
        queue.append_event(job.id, "error", message=traceback.format_exc(limit=3)[:2000])
        queue.transition(
            job.id, "failed", ("running", "cancelling", "preparing"),
            error=f"{type(exc).__name__}: {exc}", message="przebieg zakończony awarią",
        )
        return 1

    # Raport najpierw, stan potem.
    parsed = parse_report(render_json(result).encode("utf-8"))
    row, _created = repository.store_report(
        project_id=job.project_id,
        payload=parsed.payload,
        index=parsed.index,
        content_hash=parsed.content_hash,
        format_version=parsed.format_version,
        origin="engine",
        source_label=f"zadanie {job.id}",
        job_id=job.id,
    )
    queue.append_event(
        job.id,
        "report_stored",
        message=f"decyzja {row.verdict}, znalezisk: {row.finding_count}",
    )
    if not queue.transition(
        job.id,
        "completed",
        ("running", "cancelling"),
        run_id=row.run_id,
        message=f"zakończone: {row.verdict}",
    ):  # pragma: no cover - wyścig rozstrzygnięty na korzyść anulowania
        queue.append_event(
            job.id, "note", message="raport zapisany, ale zadanie było już rozliczone"
        )
    return 0


def _build_request(payload: dict[str, Any], workspace: Path) -> RunRequest:
    policy = payload["policy"]
    revision = PolicyRevision(
        id=int(policy.get("revision_id") or 0),
        profile_id=int(policy.get("profile_id") or 0),
        revision=int(policy.get("revision") or 0),
        state="active",
        policy_yaml=str(policy["policy_yaml"]),
        exceptions_yaml=policy.get("exceptions_yaml"),
        scope_map_yaml=policy.get("scope_map_yaml"),
        content_hash=str(policy.get("content_hash") or ""),
        author=None,
        note=None,
        created_at="",
        activated_at=None,
    )
    snapshot = materialize(revision, workspace / "polityka")
    scope = payload["scope"]
    gates = payload.get("gates")
    return RunRequest(
        repo=Path(payload["project"]["repo_path"]),
        # SHA, nie nazwa gałęzi: gałąź mogła się przesunąć od zatwierdzenia
        # formularza, a zadanie ma wykonać zamrożony zakres.
        base=str(scope["merge_base"]),
        head=str(scope["head_sha"]),
        policy_path=snapshot.policy_path,
        exceptions_path=snapshot.exceptions_path,
        scope_map_path=snapshot.scope_map_path,
        ticket=payload.get("ticket"),
        gates=tuple(gates) if gates else None,
        fast_path=bool(payload.get("fast_path", True)),
    )


def _record(queue: JobQueue, job_id: int, event: ProgressEvent) -> None:
    """Zdarzenie postępu → wiersz w bazie. Działa w procesie workera."""
    queue.append_event(
        job_id,
        event.kind,
        message=event.message[:500],
        gate=event.gate,
        completed=event.completed,
        total=event.total,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
