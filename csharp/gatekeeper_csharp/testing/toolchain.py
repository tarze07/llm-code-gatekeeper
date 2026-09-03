"""`TestToolchain` (`gatekeeper_core.core.plugins`) dla C#.

Odpowiednik `testing/toolchain.py` w python-packu — jeden zarejestrowany
dostawca (`gatekeeper.test_toolchains`) łączący discovery (Roslyn, przez
`gatekeeper-cs-helper`), jakość testów (ten sam helper), weryfikację
krzyżową (`dotnet test` + TRX) i pokrycie różnicowe (coverlet Cobertura +
`core.diffcover`). Architektura i uzasadnienia decyzji: `../PLAN-G2.md`
w tym repo.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from gatekeeper_core.adapters.base import ToolFailed, run_tool
from gatekeeper_core.adapters.dotnet_projects import projects_for
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.diffcover import DiffCoverageResult, run_diff_cover_on_report
from gatekeeper_core.core.plugins import ToolchainIsolationBroken
from gatekeeper_core.core.runner import Sandbox, SandboxPolicy

from . import discovery, quality
from .discovery import TestItem
from .quality import QualityIssue
from .trx_runner import RunOutput, TestOutcome, run_dotnet_test

CODE_LANGUAGES = {"csharp"}


class IsolationBroken(ToolchainIsolationBroken):
    pass


class CsharpTestToolchain:
    language = "csharp"

    # ------------------------------------------------------------ discovery

    def discover_tests(self, change: ChangeContext) -> list[TestItem]:
        test_paths = [
            f.path for f in change.files if f.test and f.status != "D" and f.path.endswith(".cs")
        ]
        if not test_paths:
            return []
        with change.worktree_at(change.base_sha) as base_worktree:
            base_items = discovery.discover_tests(
                base_worktree, self._existing(base_worktree, test_paths)
            )
        head_items = discovery.discover_tests(change.repo, self._existing(change.repo, test_paths))
        return discovery.changed_tests(base_items, head_items)

    @staticmethod
    def _existing(root: Path, relative_paths: list[str]) -> list[str]:
        # Test dopisany w tym PR-ze nie istnieje jeszcze na bazie — helper
        # dostaje tylko ścieżki, które faktycznie tam są (nowy plik w base_items
        # po prostu się nie pojawi, co `changed_tests` poprawnie liczy jako "nowy").
        return [p for p in relative_paths if (root / p).is_file()]

    # -------------------------------------------------------------- quality

    def lint_quality(
        self, change: ChangeContext, tests: list[TestItem]
    ) -> list[tuple[TestItem, QualityIssue]]:
        if not tests:
            return []
        files = sorted({item.file for item in tests})
        issues_by_nodeid = quality.lint_quality(change.repo, files)
        return [
            (item, issue)
            for item in tests
            for issue in issues_by_nodeid.get(item.nodeid, [])
        ]

    # ---------------------------------------------------------- cross-verify

    def run_cross_verify(
        self,
        change: ChangeContext,
        tests: list[TestItem],
        config: dict[str, Any],
    ) -> tuple[dict[str, TestOutcome], str]:
        """Zwraca `(outcomes, message)` — `outcomes` to `dict[nodeid, TestOutcome]`
        z `trx_runner`. Rzuca `IsolationBroken`/`DotnetTestUnavailable`, tak
        jak `PythonTestToolchain` (python-pack) — gate (core) łapie typy bazowe
        (`ToolchainIsolationBroken`/`ToolchainUnavailable`), nie te podklasy."""
        sandbox = Sandbox(
            SandboxPolicy(
                network=False,
                timeout_s=float(config.get("timeout_s", 600.0)),
                # CoreCLR rezerwuje kilka GB przestrzeni adresowej na starcie
                # (ten sam kwirk co `adapters/dotnet.py::CsharpStaticChecker`)
                memory_mb=None,
                keep_env=tuple(config.get("keep_env", ())),
            )
        )
        with change.worktree_at(change.base_sha) as worktree:
            overlaid = self._overlay_tests(change, worktree)
            self._assert_no_stale_build_artifacts(worktree)
            projects = projects_for(worktree, [t.file for t in tests])
            result: RunOutput = run_dotnet_test(
                worktree,
                sandbox,
                projects=projects,
                nodeids=[t.nodeid for t in tests],
                timeout_s=float(config.get("timeout_s", 600.0)),
            )
        return result.outcomes, f"nałożono {overlaid} plików testowych w {len(projects)} projektach"

    def _overlay_tests(self, change: ChangeContext, worktree: Path) -> int:
        """Do worktree na starym kodzie wnosimy *wyłącznie* pliki testowe —
        skopiowanie czegokolwiek z kodu produkcyjnego unieważnia cały test,
        tak samo jak w `PythonTestToolchain._overlay_tests`."""
        count = 0
        for file in change.files:
            if not file.test or file.status == "D":
                continue
            content = change.file_at(change.head_sha, file.path)
            if content is None:
                continue
            target = worktree / file.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            count += 1
        return count

    def _assert_no_stale_build_artifacts(self, worktree: Path) -> None:
        """`worktree` pochodzi ze świeżego `git worktree add --detach` — nie
        powinien mieć żadnego `bin/`/`obj/` (gitignored, nigdy nie budowany
        wcześniej pod tą ścieżką). Jeśli jednak ma (np. `<BaseIntermediateOutputPath>`
        w `.csproj` wskazujące poza worktree, albo katalog reużyty), build
        mógłby po cichu skorzystać z artefaktów zawierających już nowy kod —
        PLAN-G2.md §4.3 opisuje, czym to ryzyko różni się od Pythona
        (tam: `pip install -e .`, tu: inkrementalny `obj`/`bin`)."""
        stale = [p for p in worktree.rglob("obj") if p.is_dir()] + [
            p for p in worktree.rglob("bin") if p.is_dir()
        ]
        if stale:
            raise IsolationBroken(
                f"worktree kodu bazowego zawiera {len(stale)} katalogów bin/obj sprzed "
                "tego przebiegu — build mógłby po cichu przeciekać skompilowany nowy kod "
                "zamiast mierzyć stary. Sprawdź `<BaseIntermediateOutputPath>`/"
                "`<BaseOutputPath>` w plikach projektu ocenianego repo."
            )

    # ------------------------------------------------------------- coverage

    def produce_coverage_report(
        self, change: ChangeContext, config: dict[str, Any]
    ) -> DiffCoverageResult:
        sandbox = Sandbox(
            SandboxPolicy(
                network=False,
                timeout_s=float(config.get("timeout_s", 600.0)),
                memory_mb=None,
                keep_env=tuple(config.get("keep_env", ())),
            )
        )
        timeout_s = float(config.get("timeout_s", 600.0))
        # Celowo **wszystkie** projekty testowe repo, nie tylko dotknięte
        # diffem (`run_cross_verify` robi to dla nowych/zmienionych) — pytanie
        # tu brzmi „czy diff pokrywa *jakikolwiek* test", tak samo jak w
        # `PythonTestToolchain.produce_coverage_report`. `dotnet test` bez
        # wskazanego projektu/solucji operuje myląco na czymkolwiek zastanym
        # w `cwd` (patrz PLAN-G2.md, ten sam problem co w `run_cross_verify`),
        # więc iterujemy `.csproj` jawnie; projekt bez testów kończy się
        # nieszkodliwie (`dotnet test` raportuje "brak testów", nie błąd).
        with tempfile.TemporaryDirectory(prefix="gatekeeper-cs-coverage-") as tmp:
            reports: list[Path] = []
            for project in sorted(change.repo.rglob("*.csproj")):
                if "obj" in project.parts or "bin" in project.parts:
                    continue
                results_dir = Path(tmp) / project.stem
                try:
                    run_tool(
                        [
                            "dotnet",
                            "test",
                            str(project),
                            "--collect:XPlat Code Coverage;Format=cobertura",
                            "--results-directory",
                            str(results_dir),
                        ],
                        change.repo,
                        sandbox,
                        timeout_s,
                        # testy mogą być czerwone bez winy tej bramki — coverlet i tak
                        # zebrało dane wykonania; liczy się, że `dotnet test` samo nie padło.
                        ok_returncodes=(0, 1),
                    )
                except ToolFailed:
                    continue  # projekt bez testów/bez coverlet — nie ma czego zebrać
                reports.extend(sorted(results_dir.glob("**/coverage.cobertura.xml")))

            if not reports:
                return DiffCoverageResult()
            return run_diff_cover_on_report(
                change.repo, sandbox, reports, change.base_sha, timeout_s
            )


__all__ = ["CsharpTestToolchain", "IsolationBroken"]
