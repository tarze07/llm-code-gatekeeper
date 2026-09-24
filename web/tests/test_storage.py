"""Baza panelu: migracje, transakcje, odczyt w trakcie zapisu."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from conftest import load_sample

from gatekeeper_web.services.reports import parse_report
from gatekeeper_web.storage import Database, ReportConflict, Repository, RunFilter
from gatekeeper_web.storage.db import SCHEMA_VERSION, SchemaTooNew


def zbuduj(tmp_path: Path) -> Repository:
    return Repository(Database(tmp_path / "panel.db"))


def test_migracja_zapisuje_wersje(tmp_path: Path) -> None:
    db = Database(tmp_path / "panel.db")
    with db.reading() as conn:
        wersja = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()["v"]
    assert wersja == SCHEMA_VERSION


def test_ponowne_otwarcie_bazy_nie_powtarza_migracji(tmp_path: Path) -> None:
    Database(tmp_path / "panel.db")
    db = Database(tmp_path / "panel.db")
    with db.reading() as conn:
        ile = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert ile == SCHEMA_VERSION


def test_baza_z_nowszym_schematem_nie_jest_otwierana(tmp_path: Path) -> None:
    sciezka = tmp_path / "panel.db"
    Database(sciezka)
    with sqlite3.connect(sciezka) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, '2030-01-01')",
            (SCHEMA_VERSION + 5,),
        )
    # Migracja w dół nie istnieje, więc panel woli odmówić startu niż zgadywać.
    with pytest.raises(SchemaTooNew):
        Database(sciezka)


def test_slug_projektu_jest_unikalny(tmp_path: Path) -> None:
    repository = zbuduj(tmp_path)
    pierwszy = repository.create_project("Taskboard")
    drugi = repository.create_project("Taskboard")

    assert pierwszy.slug == "taskboard"
    assert drugi.slug == "taskboard-2"


def test_konflikt_raportu_nie_nadpisuje_historii(tmp_path: Path) -> None:
    repository = zbuduj(tmp_path)
    project = repository.create_project("Demo")
    oryginal = parse_report(json.dumps(load_sample("bez-znalezisk.json")).encode("utf-8"))
    repository.store_report(
        project.id, oryginal.payload, oryginal.index, oryginal.content_hash, "core/v0"
    )

    podmieniony = load_sample("bez-znalezisk.json")
    podmieniony["decision"]["verdict"] = "BLOCK"
    inny = parse_report(json.dumps(podmieniony).encode("utf-8"))

    with pytest.raises(ReportConflict):
        repository.store_report(
            project.id, inny.payload, inny.index, inny.content_hash, "core/v0"
        )

    zapisany = repository.get_report(project.id, "0000demo0003")
    assert zapisany is not None
    assert zapisany.verdict == "PASS"


def test_odczyt_w_trakcie_zapisu_nie_wywala_sie(tmp_path: Path) -> None:
    repository = zbuduj(tmp_path)
    project = repository.create_project("Demo")
    parsed = parse_report(json.dumps(load_sample("demo-celowe-usterki.json")).encode("utf-8"))

    bledy: list[BaseException] = []

    def czytaj() -> None:
        try:
            for _ in range(50):
                repository.count_reports(RunFilter(project_id=project.id))
                repository.list_reports(RunFilter(project_id=project.id))
        except BaseException as exc:  # pragma: no cover - to jest asercja wątku
            bledy.append(exc)

    czytelnik = threading.Thread(target=czytaj)
    czytelnik.start()
    for i in range(10):
        payload = dict(parsed.payload, run_id=f"przebieg-{i}")
        repository.store_report(
            project.id, payload, parsed.index, f"{parsed.content_hash}-{i}", "core/v0"
        )
    czytelnik.join()

    assert bledy == []
    assert repository.count_reports(RunFilter(project_id=project.id)) == 10


def test_slad_dzialan_rejestruje_import(tmp_path: Path) -> None:
    repository = zbuduj(tmp_path)
    project = repository.create_project("Demo")
    parsed = parse_report(json.dumps(load_sample("bez-znalezisk.json")).encode("utf-8"))
    repository.store_report(
        project.id, parsed.payload, parsed.index, parsed.content_hash, "core/v0",
        source_label="bez-znalezisk.json",
    )

    rodzaje = [event["kind"] for event in repository.audit_tail()]
    assert "project.created" in rodzaje
    assert "report.imported" in rodzaje
