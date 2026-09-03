"""Wywołanie `diff-cover` na już-gotowym raporcie pokrycia (Cobertura/lcov/JaCoCo)
i sparsowanie jego JSON-a — część wspólna `G2.diff_coverage` dla wszystkich
toolchainów.

Wydzielone z `python`-packu (`adapters/coverage.py`) w momencie, gdy pojawił się
drugi konsument (`csharp`-pack, PLAN-G2.md §5) — dopóki był tylko jeden
toolchain, ta ekstrakcja byłaby przedwczesna abstrakcją. `diff-cover` samo w
sobie jest już language-agnostic (wspiera Cobertura/lcov/JaCoCo/Clover, patrz
https://pypi.org/project/diff-cover/) — python-pack woła je na Cobertorze z
`coverage.py`, csharp-pack na Cobertorze z coverlet (`dotnet test
--collect:"XPlat Code Coverage;Format=cobertura"`). **Produkcja** raportu
(uruchomienie testów pod narzędziem pokrycia danego języka) zostaje w każdym
pack'u — to jedyna część, która naprawdę różni się między toolchainami.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.base import ToolFailed, run_tool
from .runner import Sandbox


@dataclass(frozen=True)
class FileCoverage:
    covered: int
    total: int

    @property
    def ratio(self) -> float | None:
        return self.covered / self.total if self.total else None


@dataclass(frozen=True)
class DiffCoverageResult:
    #: Wszystkie pliki z diffa, które `diff-cover` zmierzył — **łącznie z testami**.
    #: Filtrowanie do kodu produkcyjnego to sprawa wywołującego (potrzebuje
    #: `ChangeContext.is_test_file`, którego ten moduł celowo nie zna).
    files: dict[str, FileCoverage] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def parse_diff_cover_json(payload: str) -> DiffCoverageResult:
    """Czysta funkcja parsująca — testowana na zapisanej próbce (`tests/data/`)."""
    data = json.loads(payload) if payload.strip() else {}
    files: dict[str, FileCoverage] = {}
    for path, stats in (data.get("src_stats") or {}).items():
        covered = len(stats.get("covered_lines") or [])
        violations = len(stats.get("violation_lines") or [])
        files[path] = FileCoverage(covered=covered, total=covered + violations)
    return DiffCoverageResult(files=files, raw=data)


def run_diff_cover_on_report(
    repo: Path,
    sandbox: Sandbox,
    report_paths: list[Path],
    base_sha: str,
    timeout_s: float,
) -> DiffCoverageResult:
    """`diff-cover <raport(y)> --compare-branch=<base_sha>` → `DiffCoverageResult`.

    `report_paths` przyjmuje więcej niż jeden plik, bo niektóre toolchainy
    (np. `dotnet test` na rozwiązaniu z wieloma projektami testowymi)
    produkują raport per projekt, nie jeden zbiorczy — `diff-cover` sam scala
    wiele `--format` wejść w jednym przebiegu (analogia do `run_semgrep`
    scalającego wiele `--config`, `adapters/semgrep.py`).

    Brak zmian w diffie objętych żadnym raportem albo problem z zakresem
    porównania to fakt o tym PR-ze (`DiffCoverageResult()` pusty), nie
    awaria narzędzia — `ToolFailed` od `diff-cover` jest tu łapane, nie
    propagowane.
    """
    if not report_paths:
        return DiffCoverageResult()
    with tempfile.TemporaryDirectory(prefix="gatekeeper-diffcov-") as tmp:
        json_report = Path(tmp) / "diffcover.json"
        try:
            run_tool(
                [
                    "diff-cover",
                    *(str(p) for p in report_paths),
                    f"--compare-branch={base_sha}",
                    "--branch-coverage",
                    "--total-percent-float",
                    f"--format=json:{json_report}",
                    "--quiet",
                ],
                repo,
                sandbox,
                timeout_s,
            )
        except ToolFailed:
            return DiffCoverageResult()

        if not json_report.exists():
            return DiffCoverageResult()
        return parse_diff_cover_json(json_report.read_text(encoding="utf-8"))
