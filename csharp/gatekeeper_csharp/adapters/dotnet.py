"""Adapter .NET: `dotnet build` (G1.static).

`dotnet build` pełni dla C# podwójną rolę ruff+mypy naraz — sam kompilator
Roslyna jest kontrolą typów w trybie ścisłym, więc nie ma osobnego narzędzia
do wywołania. Format diagnostyk jest ten sam co u tsc (MSBuild i tsc dzielą
konwencję `plik(linia,kolumna): poziom KODxxxx: treść`), z dopisanym na końcu
projektem źródłowym w nawiasach kwadratowych — stąd wspólny parser w
`adapters/base.py::parse_compiler_diagnostics`.

Odnajdywanie projektów (`find_project_for`/`projects_for`) i skan podatności
(`dotnet list package --vulnerable`) żyją w `adapters/dotnet_projects.py`
(core) — są potrzebne też `deps.ecosystems.NuGetEcosystem` (G3.sca), nie tylko
temu adapterowi.

Każda kopia commita wymaga własnego `dotnet restore`. Restore korzysta
z lokalnego cache NuGet udostępnionego przez sandbox, z pustą listą źródeł
pakietów i bez sieci. Brak pakietu w cache jest błędem kontroli.

**Sandbox musi dostać `memory_mb=None`.** CoreCLR (runtime .NET-a) rezerwuje
kilka GB przestrzeni adresowej na starcie niezależnie od realnego zużycia —
dokładnie ten sam kwirk co silnik OCaml semgrepa (`gates/g3_sast.py`).
Twardy `RLIMIT_AS` z domyślnej `SandboxPolicy` zabija `dotnet` kodem 137
(`GC heap initialization failed`) zamiast czytelnego błędu, zweryfikowane
na żywym SDK, nie z dokumentacji.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from gatekeeper_core.adapters.base import (
    ToolFailed,
    ToolMissing,
    parse_compiler_diagnostics,
    run_tool,
)
from gatekeeper_core.adapters.dotnet_projects import DOTNET, projects_for
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.finding import Finding, Severity
from gatekeeper_core.core.plugins import StaticCheckOutcome
from gatekeeper_core.core.runner import Sandbox, SandboxPolicy


def dotnet_build_scenario(level: str, code: str, message: str) -> str:
    return (
        f"Kompilator C# zgłasza `{code}`: {message}. W kodzie od agenta ta klasa błędów "
        "zwykle oznacza wywołanie API, którego nie ma, albo argument o innym typie — czyli "
        "kod, który nie da się nawet zbudować, nie mówiąc o uruchomieniu."
    )


def parse_dotnet_build(payload: str, repo: Path, gate: str) -> list[Finding]:
    def severity_of(level: str, code: str) -> Severity:
        return Severity.HIGH if level == "error" else Severity.MEDIUM

    return parse_compiler_diagnostics(
        payload, repo, gate, "dotnet", dotnet_build_scenario, severity_of
    )


def run_dotnet_build(
    repo: Path,
    sandbox: Sandbox,
    gate: str,
    project: str,
    timeout_s: float = 180.0,
    args: list[str] | None = None,
) -> list[Finding]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=repo, prefix="gatekeeper-restore-") as tmp:
        config = Path(tmp) / "NuGet.Config"
        config.write_text(
            '<configuration><packageSources><clear /></packageSources></configuration>',
            encoding="utf-8",
        )
        run_tool(
            [DOTNET, "restore", project, "--configfile", str(config),
             "-p:NuGetAudit=false", "-v", "quiet"],
            repo, sandbox, timeout_s,
        )
    remaining = timeout_s - (time.monotonic() - started)
    if remaining <= 0:
        raise ToolFailed("dotnet restore wykorzystał budżet kontroli kompilacji")
    command = [
        DOTNET,
        "build",
        project,
        "--no-restore",
        "-v",
        "quiet",
        "/clp:NoSummary",
        *(args or []),
    ]
    # dotnet build: 0 = czysto (mogą być ostrzeżenia), 1 = błędy kompilacji
    result = run_tool(command, repo, sandbox, remaining, ok_returncodes=(0, 1))
    findings = parse_dotnet_build(result.stdout + "\n" + result.stderr, repo, gate)
    # Błąd MSBuild/SDK nie dowodzi poprawności kodu. Diagnostyki infrastruktury
    # (np. NETSDK1004) i nieparsowalne wyjście nie mogą zniknąć w filtrze diffa.
    errors = [f for f in findings if f.severity >= Severity.HIGH]
    if result.returncode and (
        not errors or any(not f.rule_id.startswith("dotnet.CS") for f in errors)
    ):
        raise ToolFailed(f"dotnet build nie sprawdził kodu: {result.tail()}")
    return findings


class CsharpStaticChecker:
    """`StaticChecker` dla C#: `dotnet build`. 1:1 przeniesienie dawnej
    `g1_static.py::StaticGuard._run_csharp`."""

    checker_id = "csharp"
    languages = ("csharp",)

    def empty_facts(self) -> dict[str, Any]:
        return {
            "static.csharp_files_checked": 0,
            "static.csproj_found": False,
            "static.dotnet_available": True,
        }

    def check(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> StaticCheckOutcome:
        facts = self.empty_facts()
        findings: list[Finding] = []
        cs_files = [
            f.path for f in change.effective_files if f.language == "csharp" and f.status != "D"
        ]
        facts["static.csharp_files_checked"] = len(cs_files)
        if not cs_files:
            return StaticCheckOutcome(findings=findings, facts=facts)

        projects = projects_for(change.repo, cs_files)
        facts["static.csproj_found"] = bool(projects)
        if not projects:
            return StaticCheckOutcome(findings=findings, facts=facts)

        require_dotnet = bool(config.get("require_dotnet_build", False))
        # CoreCLR rezerwuje kilka GB przestrzeni adresowej na starcie
        # niezależnie od realnego zużycia — jak silnik OCaml semgrepa
        # (gates/g3_sast.py). Twardy RLIMIT_AS zabija `dotnet` kodem 137
        # zamiast czytelnym błędem; zweryfikowane na żywym SDK.
        sandbox = Sandbox(
            SandboxPolicy(
                network=False,
                timeout_s=budget_s,
                memory_mb=None,
                keep_env=tuple(config.get("keep_env", ())),
            )
        )

        for project in projects:
            try:
                findings.extend(
                    run_dotnet_build(
                        change.repo,
                        sandbox,
                        gate_id,
                        project=project,
                        timeout_s=budget_s / max(len(projects), 1),
                        args=list(config.get("dotnet_args", [])),
                    )
                )
            except ToolMissing as exc:
                facts["static.dotnet_available"] = False
                if require_dotnet:
                    return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
                break  # brak `dotnet` raz = brak dla wszystkich projektów
            except ToolFailed as exc:
                return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
        return StaticCheckOutcome(findings=findings, facts=facts)
