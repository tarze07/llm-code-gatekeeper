"""Uruchamianie testów TS/JS i czytanie wyniku per test — odpowiednik
`testing/pytest_runner.py` (python-pack, JUnit XML) i `testing/trx_runner.py`
(csharp-pack, TRX).

Format wyniku: **JSON jesta**. Vitest emituje ten sam kształt pod
`--reporter=json`, więc jeden parser obsługuje oba runnery — to jedyny
powód, dla którego wsparcie dwóch frameworków naraz nie kosztuje tu
podwójnie. Zakres v1 to vitest i jest; mocha/`node:test`/ava to rozszerzenie
listy w `detect_runner()` plus drugi parser, czyli zmiana rozmiaru, nie
architektury (dokładnie tak samo, jak helper C# obsługuje na razie sam xUnit).

Dlaczego uruchamiamy **całe pliki testowe**, a nie pojedyncze testy przez
`--testNamePattern`: nazwa testu w TS/JS jest dowolnym stringiem (nawiasy,
`$`, znaki regexowe, interpolacja w `it.each`), więc filtr po nazwie
wymagałby escapowania, które i tak nie pokrywa `each`. Zamiast tego
uruchamiamy pliki i **korelujemy po nodeid** odtworzonym z `ancestorTitles`
+ `title` w JSON-ie — tym samym stringiem, który liczy `helper.cjs`.
Koszt: na starym kodzie wykonują się też testy spoza diffa (ich wynik jest
ignorowany); zysk: zero kruchości filtra. `dotnet test --filter` w C# jest
możliwy tylko dlatego, że tam nazwa testu to identyfikator języka.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gatekeeper_core.core.plugins import ToolchainUnavailable
from gatekeeper_core.core.runner import Sandbox, SandboxUnavailable

from ..adapters.linters import resolve_bin

Outcome = Literal["passed", "failed", "error", "skipped", "missing"]

Runner = Literal["vitest", "jest"]

#: Kolejność ma znaczenie tylko wtedy, gdy repo deklaruje oba (rzadkie, ale
#: spotykane w trakcie migracji) — wtedy wygrywa vitest, bo to on jest
#: zwykle celem migracji, a nie tym, z czego się schodzi.
_RUNNER_PACKAGES: tuple[Runner, ...] = ("vitest", "jest")


class TestRunnerUnavailable(ToolchainUnavailable):
    """Ani vitest, ani jest nie są zadeklarowane w `package.json` ocenianego
    repo — albo runner jest, ale nie dał się uruchomić.

    Świadomie **fail-closed**: bramka zwraca `error`, nie cichy `pass`.
    Repo na mocha/`node:test` dostanie ten błąd dopiero wtedy, gdy PR
    faktycznie dołoży nowy test (bez nowych testów `run_cross_verify` nie
    jest w ogóle wołane), a polityka może to złagodzić `warn_only:
    [G2.cross_verify]` do czasu dopisania obsługi jego runnera.
    """

    # Nazwa zaczyna się od „Test" — to wyjątek, nie przypadek testowy pytesta.
    __test__ = False


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False

    nodeid: str
    outcome: Outcome
    message: str = ""


@dataclass
class RunOutput:
    outcomes: dict[str, TestOutcome]
    returncode: int
    stdout: str
    stderr: str


def detect_runner(root: Path) -> Runner:
    """Runner zadeklarowany w `package.json` repo (`dependencies`,
    `devDependencies` albo treść `scripts.test`)."""
    manifest = root / "package.json"
    if not manifest.is_file():
        raise TestRunnerUnavailable(
            "oceniane repo nie ma `package.json` w korzeniu — nie da się ustalić, "
            "czym uruchomić testy TS/JS"
        )
    try:
        data: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TestRunnerUnavailable(f"nie da się odczytać `package.json`: {exc}") from exc

    declared = {
        *(data.get("dependencies") or {}),
        *(data.get("devDependencies") or {}),
    }
    test_script = str((data.get("scripts") or {}).get("test") or "")
    for runner in _RUNNER_PACKAGES:
        if runner in declared or runner in test_script:
            return runner
    raise TestRunnerUnavailable(
        "w `package.json` nie ma ani `vitest`, ani `jest` — pack TS/JS potrafi dziś "
        "uruchomić tylko te dwa runnery, więc nie ma jak udowodnić, że nowe testy "
        "faktycznie coś sprawdzają"
    )


def _command(runner: Runner, bin_path: str, files: list[str], report: Path) -> list[str]:
    if runner == "vitest":
        # `run` wyłącza tryb watch (domyślny w terminalu interaktywnym).
        return [
            bin_path,
            "run",
            "--reporter=json",
            f"--outputFile={report}",
            *files,
        ]
    return [bin_path, "--ci", "--json", f"--outputFile={report}", *files]


def run_tests(
    worktree: Path,
    sandbox: Sandbox,
    nodeids: list[str],
    files: list[str],
    timeout_s: float = 600.0,
    runner: Runner | None = None,
) -> RunOutput:
    """Uruchamia wskazane pliki testowe i mapuje wynik na `nodeids`.

    Test, którego runner w ogóle nie zaraportował, dostaje `"missing"`; test
    w pliku, który nie dał się załadować (typowo: importuje funkcję, której
    stary kod jeszcze nie ma), dostaje `"error"` — to rozróżnienie jest
    całym sensem punktu 3 z docstringa `gates/g2_crossverify.py`: porażka
    asercji dowodzi czegoś o zachowaniu, błąd importu znacznie mniej.
    """
    if not nodeids or not files:
        return RunOutput({}, 0, "", "")
    chosen: Runner = runner if runner is not None else detect_runner(worktree)
    bin_path = resolve_bin(worktree, chosen)

    with tempfile.TemporaryDirectory(prefix="gatekeeper-ts-tests-") as tmp:
        report = Path(tmp) / "results.json"
        command = _command(chosen, bin_path, files, report)
        try:
            result = sandbox.run(command, cwd=worktree, timeout_s=timeout_s)
        except SandboxUnavailable as exc:
            raise TestRunnerUnavailable(str(exc)) from exc
        except FileNotFoundError as exc:
            raise TestRunnerUnavailable(
                f"`{chosen}` jest zadeklarowany w `package.json`, ale nie ma go w "
                "`node_modules/.bin` ani na PATH — czy w ocenianym repo zrobiono `npm install`?"
            ) from exc
        if result.timed_out:
            raise TestRunnerUnavailable(f"`{chosen}` przekroczył limit {timeout_s:g}s")

        payload = report.read_text(encoding="utf-8") if report.exists() else result.stdout
        outcomes = parse_report(payload, worktree, expected=nodeids)

    for nodeid in nodeids:
        outcomes.setdefault(nodeid, TestOutcome(nodeid, "missing"))
    return RunOutput(outcomes, result.returncode, result.stdout, result.stderr)


def parse_report(payload: str, root: Path, expected: list[str]) -> dict[str, TestOutcome]:
    """JSON jesta/vitesta → mapa nodeid → wynik. Czysta funkcja, testowana
    na zapisanej próbce (`tests/data/`), tak jak `parse_trx` w csharp-packu.

    Testy spoza `expected` (istniejące testy w tym samym pliku, przypadki
    `each`) są pomijane, nie są błędem.
    """
    outcomes: dict[str, TestOutcome] = {}
    if not payload.strip():
        return outcomes
    try:
        data: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError:
        return outcomes

    wanted = set(expected)
    status_map: dict[str, Outcome] = {
        "passed": "passed",
        "failed": "failed",
        "pending": "skipped",
        "skipped": "skipped",
        "todo": "skipped",
        "disabled": "skipped",
    }

    for file_result in data.get("testResults") or []:
        relative = _relative(file_result.get("name") or "", root)
        assertions = file_result.get("assertionResults") or []
        if not assertions:
            # Plik nie dał się załadować/skompilować — cały jego wkład to błąd,
            # nie „brak wyniku": każdy oczekiwany test z tego pliku dostaje
            # `error`, żeby bramka policzyła go jako słaby dowód, nie jako
            # nieznaleziony (co sugerowałoby literówkę w nodeid).
            message = str(file_result.get("message") or "")[:2000]
            for nodeid in wanted:
                if nodeid.startswith(f"{relative}::"):
                    outcomes[nodeid] = TestOutcome(nodeid, "error", message)
            continue

        for assertion in assertions:
            titles = [*(assertion.get("ancestorTitles") or []), assertion.get("title") or ""]
            nodeid = f"{relative}::{' > '.join(titles)}"
            if nodeid not in wanted:
                continue
            outcome = status_map.get(str(assertion.get("status") or ""), "error")
            message = "\n".join(assertion.get("failureMessages") or [])[:2000]
            outcomes[nodeid] = TestOutcome(nodeid, outcome, message)
    return outcomes


def _relative(name: str, root: Path) -> str:
    """`testResults[].name` jest ścieżką bezwzględną; nodeid — względną."""
    if not name:
        return ""
    path = Path(name)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix().lstrip(os.sep)


__all__ = [
    "Outcome",
    "RunOutput",
    "Runner",
    "TestOutcome",
    "TestRunnerUnavailable",
    "detect_runner",
    "parse_report",
    "run_tests",
]
