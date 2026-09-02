"""Wyszukiwanie testów C# przez `gatekeeper-cs-helper discover` (Roslyn, tylko
syntax tree, bez kompilacji) — odpowiednik `testing/discovery.py` w
python-packu, tam wykonywane przez `ast.parse` bezpośrednio w Pythonie.
`gatekeeper_csharp` (pakiet Pythona) nie potrafi parsować C# samodzielnie,
stąd osobny helper .NET wołany jako podproces (PLAN-G2.md §2).

Uwaga o ścieżkach: helper woła się z `cwd=root` i dostaje ścieżki **względne**
— dzięki temu `nodeid` wraca już względny wobec repo, bez osobnego kroku
`relative_to_repo` (`adapters/base.py`) po stronie Pythona. Dwa wywołania
(base/head) muszą dostać tę samą względną ścieżkę, żeby `changed_tests`
porównywało `body_hash` tego samego testu, nie dwóch różnych nodeidów.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HELPER = "gatekeeper-cs-helper"


class HelperUnavailable(RuntimeError):
    """`gatekeeper-cs-helper` nie jest zainstalowany (`dotnet tool install
    --global gatekeeper-cs-helper`) — analogia do `ToolMissing` w
    `adapters/base.py`, ale ten moduł nie woła `run_tool`/`Sandbox`: dyskryminacja
    testu jest czystą analizą syntax tree bez efektów ubocznych, więc nie
    potrzebuje izolacji sieci/pamięci narzucanej testowanemu kodowi."""


@dataclass(frozen=True)
class TestItem:
    """Kształt zgodny z `core.plugins.DiscoveryResult`."""

    file: str
    name: str
    class_name: str | None
    nodeid: str
    lineno: int
    body_hash: str
    declared_escape: str | None


def run_helper(command: str, root: Path, relative_paths: list[str]) -> dict[str, Any]:
    if not relative_paths:
        return {}
    try:
        result = subprocess.run(
            [HELPER, command, "--files", *relative_paths],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HelperUnavailable(
            f"`{HELPER}` nie jest zainstalowany — `dotnet tool install --global {HELPER}`"
        ) from exc
    if result.returncode != 0:
        raise HelperUnavailable(
            f"`{HELPER} {command}` zakończył się błędem: {result.stderr.strip()}"
        )
    payload: dict[str, Any] = json.loads(result.stdout or "{}")
    return payload


def discover_tests(root: Path, relative_paths: list[str]) -> list[TestItem]:
    """`relative_paths`: ścieżki testowych plików `.cs` względem `root`."""
    payload = run_helper("discover", root, relative_paths)
    items = []
    for raw in payload.get("tests", []) or []:
        items.append(
            TestItem(
                file=raw["file"],
                name=raw["name"],
                class_name=raw.get("class_name"),
                nodeid=raw["nodeid"],
                lineno=raw["lineno"],
                body_hash=raw["body_hash"],
                declared_escape=raw.get("declared_escape"),
            )
        )
    return items


def changed_tests(base_items: list[TestItem], head_items: list[TestItem]) -> list[TestItem]:
    """Testy nowe albo zmienione względem wersji bazowej — po `body_hash`, nie
    po tekście (przeformatowanie nie czyni z testu nowego), tak samo jak
    `discovery.changed_tests` w python-packu."""
    base_by_id = {item.nodeid: item for item in base_items}
    out = [
        item
        for item in head_items
        if item.nodeid not in base_by_id or base_by_id[item.nodeid].body_hash != item.body_hash
    ]
    out.sort(key=lambda i: (i.file, i.lineno))
    return out
