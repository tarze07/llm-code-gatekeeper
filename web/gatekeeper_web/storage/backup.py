"""Kopia zapasowa bazy panelu.

PLAN-WEB-UI.md §8: reguły retencji i kopie zapasowe przed automatycznym
usuwaniem historii. Ten pakiet **niczego nie kasuje** — `backup()` robi
spójny zrzut SQLite (w tym WAL) do osobnego pliku.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class BackupError(RuntimeError):
    """Kopia nie powstała — baza nietknięta, plik docelowy też."""


def backup_database(source: Path, destination: Path, *, overwrite: bool = False) -> Path:
    source = source.expanduser()
    destination = destination.expanduser()
    if not source.is_file():
        raise BackupError(f"brak bazy {source}")
    if destination.exists() and not overwrite:
        raise BackupError(f"{destination} już istnieje — podaj --force, żeby nadpisać")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        src = sqlite3.connect(str(source))
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)
        tmp.replace(destination)
    except BackupError:
        raise
    except OSError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise BackupError(f"nie udało się zapisać kopii: {exc}") from exc
    return destination
