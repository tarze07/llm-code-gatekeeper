"""Wyszukiwanie testów TS/JS przez helper w Node (`tools/helper.cjs`) —
odpowiednik `testing/discovery.py` w python-packu (tam `ast.parse` wprost
w Pythonie) i `gatekeeper-cs-helper discover` w csharp-packu.

Trzy różnice wobec wariantu C#, wszystkie na korzyść tego pack'a:

1. **Brak osobnej instalacji.** Helper C# jest projektem .NET wymagającym
   `dotnet tool install --global`. Tutaj to jeden plik `.cjs` wysyłany
   w kole (`package-data` w `pyproject.toml`) — jedyne, co musi istnieć
   w systemie, to `node` (i tak wymagany przez `G1.static`) oraz
   `@typescript-eslint/parser` (i tak wymagany przez `G1.complexity`).
2. **Parser.** `PLAN-G2.md` (csharp) zakładał dla TS „TypeScript Compiler
   API". Od TypeScript 7 (port natywny) pakiet `typescript` nie eksponuje
   już `createSourceFile`/`SyntaxKind`/`forEachChild` w JS — sprawdzone na
   `typescript@7.0.2`. Helper stoi więc na ESTree z
   `@typescript-eslint/parser`, co jest dodatkowo spójne z `G1.complexity`.
3. **`nodeid` bez ścieżki modułu.** W Pythonie nodeid pytesta to
   `plik::funkcja`; tutaj `plik::describe > describe > nazwa`, bo w TS/JS
   tożsamość testu daje zagnieżdżenie `describe`, nie nazwa funkcji.
   Ten sam string odtwarza `runner.py` z `ancestorTitles` + `title`
   w JSON-ie vitesta/jesta — patrz tam, dlaczego nie używamy
   `--testNamePattern`.

Ścieżki przekazujemy **względne**, z `cwd=root` — dzięki temu `nodeid`
wraca już względny wobec repo i dwa wywołania (base/head) porównują
`body_hash` tego samego testu, nie dwóch różnych nodeidów.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ..node import node_env

HELPER_NAME = "helper.cjs"


class HelperUnavailable(RuntimeError):
    """`node` albo `@typescript-eslint/parser` niedostępne — analogia do
    `ToolMissing` (`adapters/base.py`), ale ten moduł nie woła
    `run_tool`/`Sandbox`: analiza drzewa składniowego nie wykonuje kodu
    ocenianego repo, więc nie potrzebuje izolacji sieci/pamięci narzucanej
    testom (te uruchamia dopiero `runner.py`, już w sandboksie)."""


@dataclass(frozen=True)
class TestItem:
    """Kształt zgodny z `gatekeeper_core.core.plugins.DiscoveryResult`."""

    # Nazwa zaczyna się od „Test" — to model danych, nie przypadek testowy
    # pytesta (ten sam zabieg co `__test__ = False` w `gates/g2_test_sanity.py`).
    __test__ = False

    file: str
    name: str
    suite: str | None
    nodeid: str
    lineno: int
    body_hash: str
    declared_escape: str | None


def helper_path() -> Path:
    """Ścieżka do `tools/helper.cjs` wewnątrz zainstalowanego pakietu.

    `importlib.resources`, nie `__file__.parent` — ta druga forma nie
    przeżywa instalacji z koła (ta sama pułapka, którą naprawiono
    w `adapters/semgrep.py` i `deps/typosquat.py` w core, patrz
    PODSUMOWANIE.md „Znane luki naprawione po drodze")."""
    return Path(str(resources.files("gatekeeper_ts") / "tools" / HELPER_NAME))


def run_helper(command: str, root: Path, relative_paths: list[str]) -> dict[str, Any]:
    if not relative_paths:
        return {}
    try:
        result = subprocess.run(
            ["node", str(helper_path()), command, "--files", *relative_paths],
            cwd=root,
            env=node_env(),
            capture_output=True,
            text=True,
            timeout=120.0,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HelperUnavailable(
            "`node` nie jest zainstalowany — bez niego pack TS/JS nie potrafi "
            "sparsować testów"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HelperUnavailable(f"helper `{command}` przekroczył limit 120s") from exc
    if result.returncode != 0:
        raise HelperUnavailable(
            f"helper `{command}` zakończył się błędem: {result.stderr.strip()}"
        )
    payload: dict[str, Any] = json.loads(result.stdout or "{}")
    return payload


def discover_tests(root: Path, relative_paths: list[str]) -> list[TestItem]:
    """`relative_paths`: ścieżki testowych plików `.ts`/`.tsx`/`.js`/... względem `root`."""
    payload = run_helper("discover", root, relative_paths)
    return [
        TestItem(
            file=raw["file"],
            name=raw["name"],
            suite=raw.get("suite"),
            nodeid=raw["nodeid"],
            lineno=raw["lineno"],
            body_hash=raw["body_hash"],
            declared_escape=raw.get("declared_escape"),
        )
        for raw in payload.get("tests", []) or []
    ]


def changed_tests(base_items: list[TestItem], head_items: list[TestItem]) -> list[TestItem]:
    """Testy nowe albo zmienione względem wersji bazowej — po `body_hash`, nie
    po tekście (przeformatowanie nie czyni z testu nowego), tak samo jak
    `discovery.changed_tests` w python- i csharp-packu."""
    base_by_id = {item.nodeid: item for item in base_items}
    out = [
        item
        for item in head_items
        if item.nodeid not in base_by_id or base_by_id[item.nodeid].body_hash != item.body_hash
    ]
    out.sort(key=lambda i: (i.file, i.lineno))
    return out


__all__ = [
    "HELPER_NAME",
    "HelperUnavailable",
    "TestItem",
    "changed_tests",
    "discover_tests",
    "helper_path",
    "run_helper",
]
