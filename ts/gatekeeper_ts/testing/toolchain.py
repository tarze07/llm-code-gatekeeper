"""`TestToolchain` (`gatekeeper_core.core.plugins`) dla TS/JS.

Trzeci — po Pythonie i C# — dostawca `G2.cross_verify`/`G2.test_sanity`/
`G2.diff_coverage`. Architektura i uzasadnienia decyzji: `../PLAN-G2.md`
w tym repo.

Jedyny toolchain deklarujący **dwa** języki (`languages`, nie `language`):
`vitest`/`jest` uruchamiają testy `.ts` i `.js` jednym przebiegiem i
produkują jeden raport pokrycia, więc rozbicie na dwa zarejestrowane
toolchainy oznaczałoby dwa przebiegi tego samego runnera po tym samym
repo. Core czyta to przez `plugins.toolchain_languages()`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from gatekeeper_core.adapters.base import ToolFailed, ToolMissing
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.diffcover import DiffCoverageResult, run_diff_cover_on_report
from gatekeeper_core.core.plugins import ToolchainIsolationBroken
from gatekeeper_core.core.runner import Sandbox, SandboxPolicy

from . import discovery, quality
from .discovery import TestItem
from .quality import QualityIssue
from .runner import Runner, RunOutput, TestOutcome, detect_runner, run_tests

CODE_LANGUAGES = ("typescript", "javascript")

#: Rozszerzenia, które ten toolchain w ogóle bierze pod uwagę przy discovery —
#: zgodne z `_LANGUAGES` w `core/change.py` dla `typescript`/`javascript`.
TEST_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class IsolationBroken(ToolchainIsolationBroken):
    pass


class TsTestToolchain:
    language = "typescript"
    languages = CODE_LANGUAGES

    # ------------------------------------------------------------ discovery

    def discover_tests(self, change: ChangeContext) -> list[TestItem]:
        test_paths = [
            f.path
            for f in change.files
            if f.test and f.status != "D" and f.path.endswith(TEST_SUFFIXES)
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
        # dostaje tylko ścieżki, które tam faktycznie są (nowy plik po prostu
        # nie pojawi się w `base_items`, co `changed_tests` liczy jako „nowy").
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
        """Zwraca `(outcomes, message)`. Rzuca `IsolationBroken`/
        `TestRunnerUnavailable` — gate (core) łapie typy bazowe
        (`ToolchainIsolationBroken`/`ToolchainUnavailable`), nie te podklasy."""
        timeout_s = float(config.get("timeout_s", 600.0))
        sandbox = Sandbox(
            SandboxPolicy(
                network=False,
                timeout_s=timeout_s,
                # V8 rezerwuje na starcie kilkugigabajtową przestrzeń adresową
                # (pointer compression cage), więc twardy `RLIMIT_AS` wywraca
                # Node zanim ten cokolwiek uruchomi — ten sam kwirk, dla którego
                # `CsharpTestToolchain` zdejmuje limit dla CoreCLR.
                memory_mb=None,
                keep_env=tuple(config.get("keep_env", ())),
            )
        )
        runner: Runner = detect_runner(change.repo)
        with change.worktree_at(change.base_sha) as worktree:
            overlaid = self._overlay_tests(change, worktree)
            self._assert_isolation(change, config)
            linked = self._link_node_modules(change, worktree)
            files = sorted({t.file for t in tests})
            result: RunOutput = run_tests(
                worktree,
                sandbox,
                nodeids=[t.nodeid for t in tests],
                files=files,
                timeout_s=timeout_s,
                runner=runner,
            )
        note = "`node_modules` z ocenianego repo" if linked else "`node_modules` własne worktree"
        return result.outcomes, (
            f"nałożono {overlaid} plików testowych, uruchomiono {runner} na "
            f"{len(files)} plikach ({note})"
        )

    def _overlay_tests(self, change: ChangeContext, worktree: Path) -> int:
        """Do worktree na starym kodzie wnosimy *wyłącznie* pliki testowe —
        skopiowanie czegokolwiek z kodu produkcyjnego unieważnia cały dowód,
        tak samo jak w python- i csharp-packu."""
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

    def _link_node_modules(self, change: ChangeContext, worktree: Path) -> bool:
        """Świeży `git worktree` nie ma `node_modules` (katalog jest gitignored),
        a bez niego nie ruszy ani runner, ani `import` czegokolwiek z zależności.
        Podpinamy katalog ocenianego repo dowiązaniem symbolicznym zamiast go
        kopiować — kopia potrafi mieć gigabajty i minuty.

        Bezpieczeństwo tego skrótu opiera się na `_assert_isolation`, które
        odrzuca przypadek, w którym `node_modules` prowadzi z powrotem do
        źródeł repo (workspace, `npm link`)."""
        source = change.repo / "node_modules"
        target = worktree / "node_modules"
        if not source.is_dir() or target.exists():
            return False
        os.symlink(source, target, target_is_directory=True)
        return True

    def _assert_isolation(self, change: ChangeContext, config: dict[str, Any]) -> None:
        """Odpowiednik kontroli `pip install -e .` w python-packu.

        W TS/JS ten sam defekt ma inną postać: `node_modules` z dowiązaniem
        do katalogu wewnątrz repo (npm/pnpm/yarn workspaces, `npm link`).
        Test uruchomiony w worktree kodu bazowego zaimportowałby wtedy pakiet
        z **katalogu roboczego**, czyli nowy kod — i bramka porównywałaby nowy
        kod z nowym, dając zielone „nic nie udowodniono" zamiast błędu.
        """
        if config.get("skip_isolation_check"):
            return
        node_modules = change.repo / "node_modules"
        if not node_modules.is_dir():
            return
        repo = change.repo.resolve()
        for entry in self._package_dirs(node_modules):
            if not entry.is_symlink():
                continue
            try:
                target = entry.resolve()
            except OSError:  # pragma: no cover - dowiązanie wiszące
                continue
            if not target.is_relative_to(repo) or target.is_relative_to(node_modules.resolve()):
                continue
            relative = target.relative_to(repo).as_posix()
            raise IsolationBroken(
                f"`node_modules/{entry.name}` jest dowiązaniem do `{relative}` wewnątrz "
                "ocenianego repo (workspace albo `npm link`) — testy uruchomione na kopii "
                "kodu bazowego zaimportowałyby stamtąd **nowy** kod, więc bramka "
                "porównywałaby nowy kod z nowym. Uruchom bramę na repo bez dowiązanych "
                "workspace'ów albo ustaw `skip_isolation_check: true` w polityce, świadomie "
                "rezygnując z tego dowodu."
            )

    @staticmethod
    def _package_dirs(node_modules: Path) -> list[Path]:
        """Wpisy `node_modules/<pakiet>` i `node_modules/@scope/<pakiet>` —
        bez `.bin` (same dowiązania *wewnątrz* `node_modules`, nie do repo)."""
        out: list[Path] = []
        for entry in node_modules.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.name.startswith("@") and entry.is_dir() and not entry.is_symlink():
                out.extend(child for child in entry.iterdir() if not child.name.startswith("."))
                continue
            out.append(entry)
        return out

    # ------------------------------------------------------------- coverage

    def produce_coverage_report(
        self, change: ChangeContext, config: dict[str, Any]
    ) -> DiffCoverageResult:
        timeout_s = float(config.get("timeout_s", 600.0))
        sandbox = Sandbox(
            SandboxPolicy(
                network=False,
                timeout_s=timeout_s,
                memory_mb=None,  # jak wyżej: V8 i twardy RLIMIT_AS się wykluczają
                keep_env=tuple(config.get("keep_env", ())),
            )
        )
        runner = detect_runner(change.repo)
        # Celowo **cały** zestaw testów repo, nie tylko pliki z diffa — pytanie
        # brzmi „czy zmienione linie pokrywa *jakikolwiek* test", tak samo jak
        # w python- i csharp-packu.
        with tempfile.TemporaryDirectory(prefix="gatekeeper-ts-coverage-") as tmp:
            out_dir = Path(tmp) / "coverage"
            command = _coverage_command(runner, change.repo, out_dir)
            try:
                result = sandbox.run(command, cwd=change.repo, timeout_s=timeout_s)
            except OSError as exc:
                raise ToolMissing(f"nie udało się uruchomić `{runner}`: {exc}") from exc
            if result.timed_out:
                raise ToolFailed(f"`{runner} --coverage` przekroczył limit {timeout_s:g}s")

            report = out_dir / "cobertura-coverage.xml"
            if not report.is_file():
                raise ToolMissing(
                    f"`{runner}` nie wyprodukował raportu Cobertura — "
                    + (
                        "dla vitesta wymagany jest pakiet `@vitest/coverage-v8` "
                        "(albo `@vitest/coverage-istanbul`)"
                        if runner == "vitest"
                        else "sprawdź konfigurację `collectCoverage` jesta"
                    )
                    + f". stderr: {result.stderr.strip()[:500]}"
                )
            return run_diff_cover_on_report(
                change.repo, sandbox, [report], change.base_sha, timeout_s
            )


def _coverage_command(runner: Runner, repo: Path, out_dir: Path) -> list[str]:
    from ..adapters.linters import resolve_bin

    bin_path = resolve_bin(repo, runner)
    if runner == "vitest":
        return [
            bin_path,
            "run",
            "--coverage",
            "--coverage.reporter=cobertura",
            f"--coverage.reportsDirectory={out_dir}",
        ]
    return [
        bin_path,
        "--ci",
        "--coverage",
        "--coverageReporters=cobertura",
        f"--coverageDirectory={out_dir}",
    ]


__all__ = ["CODE_LANGUAGES", "IsolationBroken", "TsTestToolchain"]
