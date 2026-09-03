"""Złożoność cyklomatyczna (McCabe) dla TS/JS przez regułę `complexity`
eslinta — nie nowy parser (PLAN-G1-complexity.md §8 w core-repo dopuszcza
to jako v1: „eslint complexity albo visitor na TypeScript Compiler API").
`eslint` jest już wymaganym narzędziem tego pack'a (`G1.static`), więc to
zero nowych zależności binarnych — jedyna dodatkowa zależność npm to
`@typescript-eslint/parser`, potrzebna, żeby w ogóle sparsować składnię
`.ts`/`.tsx` (adnotacje typów), której espree (domyślny parser eslinta)
nie rozumie.

Reguła woła się z progiem **1**, żeby wymusić raport per każda funkcja, nie
tylko powyżej progu polityki — `G1.complexity` (core) sam decyduje, co
przekracza próg z `policy/gates.yaml`.

Ograniczenie świadomie zaakceptowane: `complexity` eslinta raportuje
wyłącznie linię identyfikatora funkcji/metody (`line`), nie cały zasięg jej
ciała. `end_lineno` jest więc dociągany osobno przez dopasowanie nawiasów
klamrowych w kodzie źródłowym (najbliższe zbalansowane `{`...`}` od linii
zgłoszenia) — funkcje strzałkowe z ciałem-wyrażeniem (`x => x ? 1 : 2`, bez
`{}`) nie mają czego dopasować i dostają `end_lineno == lineno`: węższe okno
przecięcia z diffem niż idealne, ale nie błędne — takie funkcje są z reguły
jednolinijkowe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from gatekeeper_core.adapters.base import relative_to_repo
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.plugins import ComplexityOutcome, MethodComplexity

from .linters import ESLINT, resolve_bin

_MESSAGE_RE = re.compile(r"^(?P<descriptor>.+?) has a complexity of (?P<complexity>\d+)\.")
_QUOTED_NAME_RE = re.compile(r"'([^']+)'")


@dataclass(frozen=True)
class _TsMethod:
    name: str
    lineno: int
    end_lineno: int
    complexity: int
    nloc: int = 0


@lru_cache(maxsize=1)
def _global_node_modules() -> str | None:
    """`npm root -g` — dołączane do `NODE_PATH` procesu eslinta, żeby
    `require("@typescript-eslint/parser")` (specyfikator „goły", nie
    ścieżka bezwzględna — resolucja przez `exports` w `package.json`
    pakietu jest wtedy tą samą ścieżką co przy zwykłej instalacji lokalnej,
    bez odtwarzania jej ręcznie) w wygenerowanym configu w ogóle coś
    znalazł — Node nie przeszukuje globalnego `node_modules` domyślnie."""
    try:
        result = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=15, check=True
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    root = result.stdout.strip()
    return root or None


_ESLINT_CONFIG = """module.exports = [
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parser: require("@typescript-eslint/parser"),
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: { complexity: ["error", 1] },
  },
  {
    files: ["**/*.js", "**/*.jsx"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module" },
    rules: { complexity: ["error", 1] },
  },
];
"""


def _find_end_lineno(source_lines: list[str], start_lineno: int) -> int:
    """Best-effort dopasowanie nawiasów klamrowych — patrz docstring modułu.
    Naiwne wobec stringów/template literali (nie tokenizuje pełnego JS/TS),
    ale to jedyny sposób odzyskania zasięgu ciała funkcji z komunikatu
    reguły `complexity`, który niesie tylko linię identyfikatora."""
    depth = 0
    started = False
    in_string: str | None = None
    for i in range(start_lineno - 1, len(source_lines)):
        line = source_lines[i]
        j = 0
        while j < len(line):
            ch = line[j]
            if in_string:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_string:
                    in_string = None
            elif ch in ("'", '"', "`"):
                in_string = ch
            elif ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
            j += 1
    return start_lineno


def _parse_eslint_complexity(payload: str) -> list[tuple[str, list[_TsMethod]]]:
    """`(plik, [metody])` per plik z raportu eslinta — `source` w JSON-ie
    eslinta niesie pełną treść pliku, więc nie trzeba go osobno czytać."""
    try:
        data = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    out: list[tuple[str, list[_TsMethod]]] = []
    for file_result in data:
        source_lines = str(file_result.get("source") or "").splitlines()
        methods: list[_TsMethod] = []
        for item in file_result.get("messages") or []:
            if item.get("ruleId") != "complexity":
                continue
            match = _MESSAGE_RE.match(str(item.get("message") or ""))
            if match is None:
                continue
            descriptor = match.group("descriptor")
            name_match = _QUOTED_NAME_RE.search(descriptor)
            name = name_match.group(1) if name_match else descriptor
            lineno = int(item.get("line") or 1)
            end_lineno = _find_end_lineno(source_lines, lineno) if source_lines else lineno
            body = source_lines[lineno - 1 : end_lineno]
            methods.append(
                _TsMethod(
                    name=name,
                    lineno=lineno,
                    end_lineno=end_lineno,
                    complexity=int(match.group("complexity")),
                    nloc=sum(1 for line in body if line.strip()),
                )
            )
        out.append((file_result.get("filePath") or "", methods))
    return out


class TsComplexityAnalyzer:
    """`ComplexityAnalyzer` (`gatekeeper_core.core.plugins`) dla TS/JS."""

    analyzer_id = "ts"
    languages = ("typescript", "javascript")

    def empty_facts(self) -> dict[str, Any]:
        return {"complexity.ts_files_checked": 0, "complexity.eslint_available": True}

    def analyze(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> ComplexityOutcome:
        include_tests = bool(config.get("include_tests", False))
        files = [
            f.path
            for f in change.effective_files
            if f.language in self.languages
            and f.status != "D"
            and (include_tests or not change.is_test_file(f.path))
        ]
        facts = self.empty_facts()
        facts["complexity.ts_files_checked"] = len(files)
        if not files:
            return ComplexityOutcome(methods=[], facts=facts)

        eslint_bin = resolve_bin(change.repo, ESLINT)
        env = dict(os.environ)
        global_modules = _global_node_modules()
        if global_modules:
            previous = env.get("NODE_PATH")
            env["NODE_PATH"] = (
                f"{global_modules}{os.pathsep}{previous}" if previous else global_modules
            )

        with tempfile.TemporaryDirectory(prefix="gatekeeper-complexity-") as tmp:
            config_path = Path(tmp) / "eslint.config.js"
            config_path.write_text(_ESLINT_CONFIG, encoding="utf-8")
            command = [
                eslint_bin,
                "--config",
                str(config_path),
                "--no-config-lookup",
                "--format",
                "json",
                *files,
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=change.repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=budget_s,
                    check=False,
                )
            except FileNotFoundError:
                facts["complexity.eslint_available"] = False
                return ComplexityOutcome(
                    methods=[], facts=facts, error="eslint nie jest zainstalowany"
                )
            except subprocess.TimeoutExpired:
                return ComplexityOutcome(
                    methods=[], facts=facts, error=f"eslint przekroczył limit {budget_s:g}s"
                )

        methods: list[MethodComplexity] = []
        for file_path, ts_methods in _parse_eslint_complexity(result.stdout):
            relative = relative_to_repo(file_path, change.repo)
            for m in ts_methods:
                methods.append(
                    MethodComplexity(
                        file=relative,
                        name=m.name,
                        lineno=m.lineno,
                        end_lineno=m.end_lineno,
                        complexity=m.complexity,
                        nloc=m.nloc,
                    )
                )
        return ComplexityOutcome(methods=methods, facts=facts)
