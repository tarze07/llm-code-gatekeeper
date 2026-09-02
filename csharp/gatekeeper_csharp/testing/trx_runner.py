"""Uruchamianie `dotnet test` i czytanie wyniku per test z TRX — odpowiednik
`testing/pytest_runner.py` w python-packu (tam: JUnit XML z pytesta).

TRX to natywny format VSTest (`--logger trx`), wbudowany w SDK .NET, bez
dodatkowego pakietu NuGet (PLAN-G2.md §3) — węzeł `<UnitTestResult>` niesie
`outcome` (`Passed`/`Failed`/`NotExecuted`/`Error`), tak samo jak JUnit
rozróżnia porażkę asercji od błędu — tu: `Failed` (asercja) od `Error`
(zwykle błąd kompilacji po nałożeniu nowych testów na stary kod — test
odwołuje się do API, którego stary kod jeszcze nie ma).
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gatekeeper_core.core.plugins import ToolchainUnavailable
from gatekeeper_core.core.runner import Sandbox, SandboxUnavailable

Outcome = Literal["passed", "failed", "error", "skipped", "missing"]

#: Namespace TRX — bez niego `ElementTree.find()` nie trafia w żaden węzeł.
_TRX_NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}


class DotnetTestUnavailable(ToolchainUnavailable):
    pass


@dataclass(frozen=True)
class TestOutcome:
    nodeid: str
    outcome: Outcome
    message: str = ""


@dataclass
class RunOutput:
    outcomes: dict[str, TestOutcome]
    returncode: int
    stdout: str
    stderr: str


def run_dotnet_test(
    worktree: Path,
    sandbox: Sandbox,
    projects: list[str],
    nodeids: list[str],
    timeout_s: float = 600.0,
) -> RunOutput:
    """Buduje i uruchamia wyłącznie wskazane testy (`--filter`), jeden
    przebieg `dotnet test` **per projekt testowy** — `dotnet test` bez
    wskazanego projektu/solucji operuje na tym, co akurat znajdzie w `cwd`
    (myląco: może to być projekt produkcyjny, nie testowy, jeśli oba leżą
    obok siebie), więc `projects` (z `dotnet_projects.projects_for()`,
    core) jest tu wymagane, nie opcjonalne — ten sam wzorzec co pętla
    `for project in projects` w `CsharpStaticChecker.check()`.

    Świadomie **bez** `--no-build`/oddzielnego kroku `dotnet build`: `worktree`
    pochodzi z `ChangeContext.worktree_at()`, czyli jest świeżym `git worktree
    add --detach` bez żadnego wcześniejszego `bin/`/`obj/` (te katalogi są
    gitignored, więc nowy worktree ich w ogóle nie ma) — nie ma więc czego
    inkrementalnie przeciekać między przebiegami, w przeciwieństwie do
    `pip install -e .` w Pythonie. `_assert_no_stale_build_artifacts` w
    `toolchain.py` i tak to jawnie sprawdza, zamiast zakładać.
    """
    if not nodeids or not projects:
        return RunOutput({}, 0, "", "")
    filt = "|".join(f"FullyQualifiedName~{_qualified_suffix(n)}" for n in nodeids)
    outcomes: dict[str, TestOutcome] = {}
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    returncode = 0
    for project in projects:
        with tempfile.TemporaryDirectory(prefix="gatekeeper-trx-") as tmp:
            results_dir = Path(tmp)
            trx_name = "results.trx"
            command = [
                "dotnet",
                "test",
                project,
                "--filter",
                filt,
                "--logger",
                f"trx;LogFileName={trx_name}",
                "--results-directory",
                str(results_dir),
            ]
            try:
                result = sandbox.run(command, cwd=worktree, timeout_s=timeout_s)
            except SandboxUnavailable as exc:
                raise DotnetTestUnavailable(str(exc)) from exc
            if result.timed_out:
                raise DotnetTestUnavailable(f"`dotnet test` przekroczył limit {timeout_s:g}s")
            returncode = returncode or result.returncode
            stdout_parts.append(result.stdout)
            stderr_parts.append(result.stderr)
            report = results_dir / trx_name
            if report.exists():
                outcomes.update(parse_trx(report.read_text(encoding="utf-8"), expected=nodeids))

    for nodeid in nodeids:
        if nodeid not in outcomes:
            outcomes[nodeid] = TestOutcome(nodeid, "missing")
    return RunOutput(outcomes, returncode, "\n".join(stdout_parts), "\n".join(stderr_parts))


def _qualified_suffix(nodeid: str) -> str:
    """`plik.cs::Klasa.Metoda` → `Klasa.Metoda` — VSTest `--filter` woła po
    `FullyQualifiedName`, nie po ścieżce pliku (C#, w przeciwieństwie do
    Pythona, nie ma 1:1 między ścieżką a przestrzenią nazw)."""
    return nodeid.split("::", 1)[-1]


def parse_trx(payload: str, expected: list[str]) -> dict[str, TestOutcome]:
    """TRX XML → mapa nodeid → wynik (wyłącznie testy faktycznie znalezione
    w tym raporcie — wypełnienie `"missing"` dla nieobecnych to sprawa
    wywołującego, patrz `run_dotnet_test`: przy kilku projektach test spoza
    danego raportu nie znaczy jeszcze `missing`, może wystąpić w kolejnym).

    Koreluje `<Results><UnitTestResult testId=.../>` z `<TestDefinitions>
    <UnitTest id=...><TestMethod className=... name=.../></UnitTest>` przez
    `testId`, potem dopasowuje `className.name` (bez przestrzeni nazw — sam
    nodeid też jej nie niesie) do listy testów, o które pytaliśmy. Test spoza
    `expected` (np. pomocniczy `[Theory]` case) jest pomijany, nie błędem.
    """
    outcomes: dict[str, TestOutcome] = {}
    if not payload.strip():
        return outcomes
    lookup = {_qualified_suffix(n): n for n in expected}
    root = ET.fromstring(payload)

    id_to_nodeid: dict[str, str] = {}
    for unit_test in root.findall(".//t:TestDefinitions/t:UnitTest", _TRX_NS):
        method = unit_test.find("t:TestMethod", _TRX_NS)
        if method is None:
            continue
        class_name = (method.get("className") or "").rsplit(".", 1)[-1]
        key = f"{class_name}.{method.get('name')}"
        nodeid = lookup.get(key)
        if nodeid is not None:
            id_to_nodeid[unit_test.get("id", "")] = nodeid

    outcome_map: dict[str, Outcome] = {
        "Passed": "passed",
        "Failed": "failed",
        "NotExecuted": "skipped",
        "Error": "error",
    }
    for result in root.findall(".//t:Results/t:UnitTestResult", _TRX_NS):
        nodeid = id_to_nodeid.get(result.get("testId", ""))
        if nodeid is None:
            continue
        outcome = outcome_map.get(result.get("outcome") or "", "error")
        message_el = result.find("./t:Output/t:ErrorInfo/t:Message", _TRX_NS)
        message = (message_el.text or "") if message_el is not None else ""
        outcomes[nodeid] = TestOutcome(nodeid, outcome, message[:2000])
    return outcomes


__all__ = [
    "DotnetTestUnavailable",
    "Outcome",
    "RunOutput",
    "TestOutcome",
    "parse_trx",
    "run_dotnet_test",
]
