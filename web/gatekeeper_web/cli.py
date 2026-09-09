"""Uruchamianie panelu i import raportu z terminala."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_REPO_ROOTS,
    DEFAULT_STATE_DIR,
    Settings,
)
from .services.importing import import_report
from .services.reports import ReportImportError
from .storage import Database, ReportConflict, Repository

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Panel WWW bramy jakości")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 3


@app.command()
def serve(
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Domyślnie pętla zwrotna"),
    port: int = typer.Option(DEFAULT_PORT, "--port"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir", help="Katalog stanu panelu"),
    repo_root: list[Path] | None = typer.Option(
        None,
        "--repo-root",
        help="Katalog, w którym wolno rejestrować repozytoria (można podać wielokrotnie)",
    ),
    reload: bool = typer.Option(False, "--reload", help="Tryb pracy nad panelem"),
    no_supervisor: bool = typer.Option(
        False, "--no-supervisor", help="Nie uruchamiaj nadzorcy kolejki (gdy działa osobno)"
    ),
) -> None:
    """Startuje panel razem z nadzorcą kolejki.

    Nasłuch spoza pętli zwrotnej jest odmawiany: jednorazowy kod startowy
    nie zastępuje HTTPS i ról z planu §10, więc `--host 0.0.0.0` nadal
    nie jest „udostępnieniem zespołowi”.
    """
    import uvicorn

    from .auth import generate_bootstrap_code, generate_session_secret, hash_bootstrap_code

    bootstrap_code = generate_bootstrap_code()
    session_secret = generate_session_secret()
    settings = Settings(
        state_dir=state_dir.expanduser(),
        host=host,
        port=port,
        allowed_repo_roots=(
            tuple(p.expanduser() for p in repo_root) if repo_root else DEFAULT_REPO_ROOTS
        ),
        session_secret=session_secret,
        bootstrap_code=bootstrap_code,
        bootstrap_hash=hash_bootstrap_code(bootstrap_code, session_secret),
    )
    if host not in settings.allowed_hosts:
        typer.secho(
            f"odmowa startu na {host}: wersja zespołowa to HTTPS, logowanie i role "
            "(plan §10), a nie zmiana adresu nasłuchu",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    settings.ensure_state_dir()
    typer.secho(
        f"panel: http://{host}:{port}/logowanie  (stan: {settings.state_dir})",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        "repozytoria dozwolone w: " + ", ".join(str(root) for root in settings.allowed_repo_roots)
    )
    typer.echo(f"kod startowy (jednorazowy): {bootstrap_code}")
    typer.echo("wpisz go na stronie logowania — nie umieszczaj w URL ani w logach")

    from .app import create_app

    # Nadzorca jest osobnym procesem, nie zadaniem w tle serwera: silnik
    # forkuje i zakłada proces nadzorujący bez wątków, czyli dokładne
    # przeciwieństwo serwera ASGI (PLAN-WEB-UI.md §4).
    supervisor = None if no_supervisor else _start_supervisor(settings)
    try:
        if not reload:
            uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
            return

        # Proces po przeładowaniu musi odtworzyć te same ustawienia.
        variables = {
            "GATEKEEPER_WEB_STATE_DIR": str(settings.state_dir.resolve()),
            "GATEKEEPER_WEB_HOST": host,
            "GATEKEEPER_WEB_PORT": str(port),
            "GATEKEEPER_WEB_REPO_ROOTS": os.pathsep.join(
                str(root) for root in settings.allowed_repo_roots
            ),
            "GATEKEEPER_WEB_SESSION_SECRET": settings.session_secret,
            "GATEKEEPER_WEB_BOOTSTRAP_HASH": settings.bootstrap_hash,
        }
        previous = {key: os.environ.get(key) for key in variables}
        try:
            os.environ.update(variables)
            uvicorn.run(
                "gatekeeper_web.app:app_factory", factory=True, host=host, port=port,
                reload=True, reload_dirs=[str(Path(__file__).parent)], log_level="info",
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    finally:
        if supervisor is not None:
            _stop_supervisor(supervisor)


@app.command()
def supervise(
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    poll: float = typer.Option(1.0, "--poll", help="Co ile sekund zaglądać do kolejki"),
) -> None:
    """Uruchamia sam nadzorcę kolejki, bez serwera HTTP."""
    from .jobs.supervisor import Supervisor, SupervisorBusy

    settings = Settings(state_dir=state_dir.expanduser())
    settings.ensure_state_dir()
    typer.secho(f"nadzorca kolejki: {settings.state_dir}", fg=typer.colors.GREEN)
    try:
        Supervisor(settings, poll_s=poll).run_forever()
    except SupervisorBusy as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_USAGE) from exc


def _start_supervisor(settings: Settings) -> subprocess.Popen[bytes] | None:
    from .jobs.supervisor import supervisor_running

    if supervisor_running(settings):
        typer.secho("nadzorca już działa — nie uruchamiam drugiego", fg=typer.colors.YELLOW)
        return None
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gatekeeper_web.jobs.supervisor",
            "--state-dir",
            str(settings.state_dir),
        ],
        start_new_session=True,
    )
    typer.echo(f"nadzorca kolejki: proces {process.pid}")
    return process


def _stop_supervisor(process: subprocess.Popen[bytes]) -> None:
    """Zatrzymanie panelu zatrzymuje też nadzorcę — bez sierot po analizach."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover
        process.kill()
        process.wait(timeout=5)


@app.command()
def backup(
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    output: Path = typer.Option(..., "--output", "-o", help="Plik docelowy kopii SQLite"),
    force: bool = typer.Option(False, "--force", help="Nadpisz istniejący plik"),
) -> None:
    """Zapisuje spójną kopię bazy panelu. Nic nie usuwa z historii."""
    from .storage.backup import BackupError, backup_database

    settings = Settings(state_dir=state_dir.expanduser())
    try:
        path = backup_database(settings.db_path, output.expanduser(), overwrite=force)
    except BackupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    typer.secho(f"kopia: {path}", fg=typer.colors.GREEN)


@app.command("import")
def import_cmd(
    report: Path = typer.Argument(..., help="Plik raportu JSON z `gatekeeper run --format json`"),
    project: str = typer.Option(..., "--project", "-p", help="Nazwa albo slug projektu"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    create: bool = typer.Option(False, "--create", help="Załóż projekt, jeśli go nie ma"),
) -> None:
    """Importuje raport do panelu bez otwierania przeglądarki."""
    settings = Settings(state_dir=state_dir.expanduser())
    settings.ensure_state_dir()
    repository = Repository(Database(settings.db_path))

    found = repository.get_project_by_slug(project) or next(
        (p for p in repository.list_projects(include_archived=True) if p.name == project), None
    )
    if found is None:
        if not create:
            typer.secho(
                f"nie znam projektu {project!r} — dodaj `--create` albo załóż go w panelu",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(EXIT_USAGE)
        found = repository.create_project(project)

    try:
        outcome = import_report(repository, found, report.read_bytes(), report.name)
    except OSError as exc:
        typer.secho(f"nie mogę odczytać pliku: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_USAGE) from exc
    except ReportImportError as exc:
        typer.secho(f"raport odrzucony: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    except ReportConflict as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc

    typer.secho(outcome.message, fg=typer.colors.GREEN)
    typer.echo(f"projekt: {found.name} ({found.slug}) · przebieg: {outcome.row.run_id}")


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
