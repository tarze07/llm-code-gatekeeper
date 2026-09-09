"""Diagnostyka środowiska: co jest zainstalowane, a czego brakuje.

Panel uruchamia narzędzia na cudzym kodzie, więc operator musi widzieć, czym
dysponuje, **zanim** zleci kontrolę. Najważniejsze zdanie tego ekranu brzmi:
bez Bubblewrapa nie da się nic uruchomić i panel tego nie obchodzi żadną
„opcją bez izolacji" (PLAN-WEB-UI.md §8, core/SECURITY.md).

Brak narzędzia jest tu informacją, nie awarią panelu: bramka bez swojego
narzędzia zwróci `error`, czyli „brak dowodu" — i tak to jest opisane.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path
from typing import Any

from gatekeeper_core.core.runner import (
    describe_isolation,
    filesystem_isolation_available,
    network_isolation_available,
)
from gatekeeper_core.gates import all_gates

#: Grupy entry pointów, którymi pack językowy dokłada obsługę języka.
#: Lista bierze się z kontraktu pluginów core'a, nie z listy nazw w HTML-u.
PLUGIN_GROUPS = (
    ("gatekeeper.gates", "bramki"),
    ("gatekeeper.static_checkers", "statyczna analiza"),
    ("gatekeeper.semgrep_rule_packs", "paczki reguł SAST"),
    ("gatekeeper.complexity_analyzers", "złożoność"),
    ("gatekeeper.test_toolchains", "uruchamianie testów"),
    ("gatekeeper.dep_ecosystems", "ekosystemy pakietów"),
)

PACKAGES = (
    "llm-code-gatekeeper-core",
    "llm-code-gatekeeper-python",
    "llm-code-gatekeeper-ts",
    "llm-code-gatekeeper-csharp",
    "llm-code-gatekeeper-web",
)

#: Narzędzia wołane przez bramki jako podprocesy. Wersję pytamy stałym
#: argumentem — nic z tej listy nie pochodzi z żądania HTTP.
TOOLS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("git", ("--version",), "zakres zmiany (wymagane)"),
    ("bwrap", ("--version",), "izolacja procesów (wymagane)"),
    ("semgrep", ("--version",), "G3.sast"),
    ("gitleaks", ("version",), "G3.secrets"),
    ("diff-cover", ("--version",), "G2.diff_coverage"),
    ("node", ("--version",), "pack TS/JS"),
    ("npm", ("--version",), "pack TS/JS"),
    ("dotnet", ("--version",), "pack C#"),
)

VERSION_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ToolStatus:
    name: str
    purpose: str
    path: str | None
    version: str | None

    @property
    def available(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class PluginStatus:
    group: str
    label: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class _Probe:
    """Część diagnostyki niezależna od katalogu stanu — ta droga w czasie."""

    isolation_available: bool
    network_isolation: bool
    isolation_note: str
    tools: list[ToolStatus]
    plugins: list[PluginStatus]
    packages: dict[str, str | None]
    gates: list[dict[str, Any]]


@dataclass
class Environment:
    isolation_available: bool
    network_isolation: bool
    isolation_note: str
    tools: list[ToolStatus] = field(default_factory=list)
    plugins: list[PluginStatus] = field(default_factory=list)
    packages: dict[str, str | None] = field(default_factory=dict)
    gates: list[dict[str, Any]] = field(default_factory=list)
    disk_free_mb: int | None = None
    disk_total_mb: int | None = None
    state_dir: str = ""

    @property
    def can_run(self) -> bool:
        """Czy panel w ogóle ma prawo uruchomić kontrolę."""
        return self.isolation_available and self._tool("git").available

    @property
    def blockers(self) -> list[str]:
        problems: list[str] = []
        if not self._tool("git").available:
            problems.append("brak `git` — panel nie policzy zakresu zmiany")
        if not self.isolation_available:
            problems.append(
                "brak działającego Bubblewrapa — uruchamianie narzędzi jest zablokowane "
                "(`sudo apt-get install bubblewrap`); panel nie oferuje trybu bez izolacji"
            )
        if self.disk_free_mb is not None and self.disk_free_mb < 500:
            problems.append(
                f"mało miejsca na dysku ({self.disk_free_mb} MB) — każda bramka robi "
                "własną kopię ocenianego commita"
            )
        return problems

    def _tool(self, name: str) -> ToolStatus:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return ToolStatus(name=name, purpose="", path=None, version=None)


#: Wykrywanie narzędzi uruchamia po jednym podprocesie na narzędzie i sprawdza
#: Bubblewrapa. Dla ekranu diagnostycznego to w porządku, dla każdego wejścia
#: na pulpit — nie. Cache dotyczy wyłącznie tej części; miejsce na dysku liczy
#: się zawsze na świeżo, bo zmienia się w trakcie przebiegu.
CACHE_TTL_S = 60.0

_probe_cache: tuple[float, _Probe] | None = None


def _probe() -> _Probe:
    return _Probe(
        isolation_available=filesystem_isolation_available(),
        network_isolation=network_isolation_available(),
        isolation_note=describe_isolation(),
        tools=[_tool_status(name, args, purpose) for name, args, purpose in TOOLS],
        plugins=[
            PluginStatus(group=group, label=label, names=_entry_point_names(group))
            for group, label in PLUGIN_GROUPS
        ],
        packages={name: package_version(name) for name in PACKAGES},
        gates=[
            {
                "id": gate.id,
                "name": gate.name,
                "budget_s": gate.budget_s,
                "facts": len(gate.declared_facts()),
            }
            for gate in all_gates()
        ],
    )


def cached(state_dir: Path | None = None, ttl_s: float = CACHE_TTL_S) -> Environment:
    """To samo co `collect()`, ale bez ponownego odpytywania narzędzi."""
    global _probe_cache
    now = time.monotonic()
    if _probe_cache is None or now - _probe_cache[0] >= ttl_s:
        _probe_cache = (now, _probe())
    return _build(_probe_cache[1], state_dir)


def collect(state_dir: Path | None = None) -> Environment:
    """Pełne wykrywanie, bez cache. Tego używa ekran środowiska."""
    global _probe_cache
    probe = _probe()
    _probe_cache = (time.monotonic(), probe)
    return _build(probe, state_dir)


def _build(probe: _Probe, state_dir: Path | None) -> Environment:
    env = Environment(
        isolation_available=probe.isolation_available,
        network_isolation=probe.network_isolation,
        isolation_note=probe.isolation_note,
        tools=list(probe.tools),
        plugins=list(probe.plugins),
        packages=dict(probe.packages),
        gates=list(probe.gates),
        state_dir=str(state_dir) if state_dir else "",
    )
    if state_dir is not None:
        try:
            usage = shutil.disk_usage(state_dir)
            env.disk_free_mb = usage.free // (1024 * 1024)
            env.disk_total_mb = usage.total // (1024 * 1024)
        except OSError:  # pragma: no cover - katalog stanu zniknął
            pass
    return env


def _tool_status(name: str, args: tuple[str, ...], purpose: str) -> ToolStatus:
    path = shutil.which(name)
    if path is None:
        return ToolStatus(name=name, purpose=purpose, path=None, version=None)
    return ToolStatus(name=name, purpose=purpose, path=path, version=_version_of(path, args))


def _version_of(path: str, args: tuple[str, ...]) -> str | None:
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=VERSION_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    output = (proc.stdout or proc.stderr).strip().splitlines()
    return output[0][:120] if output else None


def _entry_point_names(group: str) -> tuple[str, ...]:
    return tuple(sorted(ep.name for ep in entry_points(group=group)))


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None
