"""Diagnostyka środowiska: co jest zainstalowane, a czego brakuje.

Panel uruchamia narzędzia na cudzym kodzie, więc operator musi widzieć, czym
dysponuje, **zanim** zleci kontrolę. Najważniejsze zdanie tego ekranu brzmi:
bez izolacji (Bubblewrap na Linuksie, kontener na Windows) nie da się nic
uruchomić i panel tego nie obchodzi żadną „opcją bez izolacji"
(PLAN-WEB-UI.md §8, core/SECURITY.md).

Brak narzędzia jest tu informacją, nie awarią panelu: bramka bez swojego
narzędzia zwróci `error`, czyli „brak dowodu" — i tak to jest opisane.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path
from typing import Any

from gatekeeper_core.core import container
from gatekeeper_core.core.runner import (
    SandboxUnavailable,
    backend,
    describe_isolation,
    isolation_available,
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
    # Osobne narzędzie, nie część packa: instaluje się je przez
    # `dotnet tool install --global gatekeeper-cs-helper` (USAGE.md §C#).
    # Brak wpisu na tej liście oznaczał, że trzy bramki padały na
    # „nie znaleziono programu", a ekran środowiska milczał.
    # Helper nie ma flagi wersji — wypisuje użycie, a gdy .NET stoi poza
    # ścieżką systemową i brakuje `DOTNET_ROOT`, wypisuje właśnie to.
    # W obu przypadkach kolumna mówi prawdę o tym, czy da się go wywołać.
    ("gatekeeper-cs-helper", ("--version",), "pack C#: G1.complexity, G2.*"),
)

#: Narzędzia, które w trybie kontenerowym siedzą w obrazie (container/Dockerfile),
#: a nie na hoście — ich brak na hoście nie jest brakiem.
IN_IMAGE_TOOLS = frozenset(
    {"semgrep", "gitleaks", "diff-cover", "node", "npm", "dotnet", "gatekeeper-cs-helper"}
)
#: Narzędzia dotyczące wyłącznie Bubblewrapa — w trybie kontenerowym odpada.
BWRAP_ONLY_TOOLS = frozenset({"bwrap"})

VERSION_TIMEOUT_S = 5.0

BWRAP_HINT = "`sudo apt-get install bubblewrap`"
CONTAINER_BUILD_HINT = (
    f"`docker build -t {container.DEFAULT_IMAGE} -f container/Dockerfile .`"
)


@dataclass(frozen=True)
class ToolStatus:
    name: str
    purpose: str
    path: str | None
    version: str | None
    #: Narzędzie żyje w obrazie kontenera, nie na hoście (tryb kontenerowy).
    in_image: bool = False

    @property
    def available(self) -> bool:
        return self.path is not None or self.in_image


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
    backend: str = "bwrap"
    engine_path: str | None = None
    image_name: str = ""
    image_present: bool = False
    isolation_reason: str = ""


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
    #: "bwrap" | "container" | "nieznany" (błędna wartość GATEKEEPER_SANDBOX).
    backend: str = "bwrap"
    engine_path: str | None = None
    image_name: str = ""
    image_present: bool = False
    #: Konkretny powód braku izolacji (puste, gdy izolacja działa).
    isolation_reason: str = ""

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
            problems.append(self._isolation_blocker())
        if self.disk_free_mb is not None and self.disk_free_mb < 500:
            problems.append(
                f"mało miejsca na dysku ({self.disk_free_mb} MB) — każda bramka robi "
                "własną kopię ocenianego commita"
            )
        return problems

    def _isolation_blocker(self) -> str:
        tail = "uruchamianie narzędzi jest zablokowane; panel nie oferuje trybu bez izolacji"
        if self.backend == "container":
            reason = self.isolation_reason or "silnik kontenerów albo obraz narzędzi niedostępny"
            if sys.platform == "win32":
                hint = (
                    "zainstaluj Docker Desktop (backend WSL2), zbuduj obraz "
                    f"{CONTAINER_BUILD_HINT} i sprawdź całość przez `gatekeeper container check`"
                )
            else:
                hint = (
                    f"zbuduj obraz {CONTAINER_BUILD_HINT} "
                    "i sprawdź całość przez `gatekeeper container check`"
                )
            return f"izolacja w kontenerze niedostępna: {reason}. {hint}; {tail}"
        if self.backend == "bwrap":
            if sys.platform == "win32":
                return f"Bubblewrap nie działa na Windows — użyj backendu kontenerowego; {tail}"
            return f"brak działającego Bubblewrapa ({BWRAP_HINT}); {tail}"
        return f"{self.isolation_note}; {tail}"

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


def _backend_name() -> str:
    try:
        return backend()
    except SandboxUnavailable:
        return "nieznany"


def _tools_for(chosen: str) -> list[ToolStatus]:
    tools: list[ToolStatus] = []
    for name, args, purpose in TOOLS:
        if chosen == "container":
            if name in BWRAP_ONLY_TOOLS:
                continue
            if name in IN_IMAGE_TOOLS:
                tools.append(
                    ToolStatus(name=name, purpose=purpose, path=None, version=None, in_image=True)
                )
                continue
        elif name in BWRAP_ONLY_TOOLS and sys.platform == "win32":
            continue
        tools.append(_tool_status(name, args, purpose))
    return tools


def _probe() -> _Probe:
    chosen = _backend_name()
    available = isolation_available()
    engine_path: str | None = None
    image_name = ""
    image_present = False
    reason = ""
    if chosen == "container":
        # Tylko odczyt: `engine()` i `_image_present()` mają własny cache i
        # limity czasu; żadnego kontenera ta strona nie uruchamia.
        engine_path = container.engine()
        image_name = container.image()
        image_present = available
        if not available:
            reason = container.unavailable_reason()
    return _Probe(
        isolation_available=available,
        network_isolation=network_isolation_available(),
        isolation_note=describe_isolation(),
        backend=chosen,
        engine_path=engine_path,
        image_name=image_name,
        image_present=image_present,
        isolation_reason=reason,
        tools=_tools_for(chosen),
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
        backend=probe.backend,
        engine_path=probe.engine_path,
        image_name=probe.image_name,
        image_present=probe.image_present,
        isolation_reason=probe.isolation_reason,
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
            [path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_S,
            check=False,
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
