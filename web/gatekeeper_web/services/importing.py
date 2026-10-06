"""Import raportu do projektu — wspólny dla API, formularza WWW i CLI."""

from __future__ import annotations

from dataclasses import dataclass

from ..storage import Project, ReportConflict, ReportRow, Repository
from .reports import ReportImportError, parse_report


@dataclass(frozen=True)
class ImportOutcome:
    row: ReportRow
    created: bool
    format_version: str

    @property
    def message(self) -> str:
        if self.created:
            return (
                f"Zaimportowano przebieg {self.row.run_id} "
                f"({self.row.finding_count} znalezisk, format {self.format_version})."
            )
        return (
            f"Przebieg {self.row.run_id} był już zaimportowany — nie utworzono duplikatu."
        )


def import_report(
    repository: Repository,
    project: Project,
    raw: bytes,
    source_label: str | None = None,
) -> ImportOutcome:
    """Waliduje i zapisuje raport. Powtórka jest bezpieczna, konflikt — głośny."""
    parsed = parse_report(raw)
    row, created = repository.store_report(
        project_id=project.id,
        payload=parsed.payload,
        index=parsed.index,
        content_hash=parsed.content_hash,
        format_version=parsed.format_version,
        origin="imported",
        source_label=source_label,
    )
    return ImportOutcome(row=row, created=created, format_version=parsed.format_version)


__all__ = ["ImportOutcome", "ReportConflict", "ReportImportError", "import_report"]
