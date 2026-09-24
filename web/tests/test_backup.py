"""Kopia zapasowa bazy — nic nie kasuje historii."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import SAMPLES
from typer.testing import CliRunner

from gatekeeper_web.cli import app
from gatekeeper_web.services.importing import import_report
from gatekeeper_web.storage import Database, Repository, RunFilter
from gatekeeper_web.storage.backup import BackupError, backup_database


def test_kopia_odtwarza_raport(tmp_path: Path) -> None:
    source = tmp_path / "stan" / "panel.db"
    repo = Repository(Database(source))
    project = repo.create_project("Demo")
    import_report(repo, project, (SAMPLES / "bez-znalezisk.json").read_bytes(), "bez.json")

    kopia = tmp_path / "kopie" / "panel.sqlite"
    backup_database(source, kopia)

    odtworzony = Repository(Database(kopia))
    reports = odtworzony.list_reports(RunFilter())
    assert len(reports) == 1
    assert reports[0].run_id == "0000demo0003"


def test_istniejacy_plik_wymaga_force(tmp_path: Path) -> None:
    source = tmp_path / "panel.db"
    Database(source)
    dest = tmp_path / "kopia.db"
    dest.write_bytes(b"stare")
    with pytest.raises(BackupError, match="--force"):
        backup_database(source, dest)
    assert dest.read_bytes() == b"stare"
    backup_database(source, dest, overwrite=True)
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] > 0


def test_cli_backup_zapisuje_plik(tmp_path: Path) -> None:
    state = tmp_path / "stan"
    Database(state / "panel.db")
    dest = tmp_path / "out" / "panel.db"
    wynik = CliRunner().invoke(
        app, ["backup", "--state-dir", str(state), "--output", str(dest)]
    )
    assert wynik.exit_code == 0, wynik.output
    assert dest.is_file()
    assert dest.stat().st_mode & 0o777 == 0o600
