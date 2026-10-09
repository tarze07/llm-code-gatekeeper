"""Backend izolacji przez kontener Linuksa (Docker albo Podman).

Odpowiednik Bubblewrap tam, gdzie przestrzeni nazw Linuksa nie ma (Windows)
albo gdzie operator woli kontener. Każde wywołanie narzędzia to jeden
`docker run` z tą samą polityką co `SandboxPolicy`: bez sieci (o ile nie
zażądano jej jawnie), system plików tylko do odczytu poza kopią kodu
i tmpfs, limity pamięci i procesów, bez uprawnień, nie jako root.

Narzędzia pochodzą z obrazu, nie z hosta. Kopia commita jest montowana pod
`/work`, ścieżki tylko do odczytu pod `/ro/<n>`, zapisywalne pod `/rw/<n>`.
Ścieżki hosta w argumentach i zmiennych środowiskowych są przepisywane na
ścieżki kontenera, a w wyjściu narzędzia — z powrotem na ścieżki hosta, żeby
parsery widziały to samo, co przy Bubblewrap.

Brak silnika albo obrazu to `SandboxUnavailable` — nigdy wykonanie bez izolacji.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .fsutil import is_link
from .paths import CONTAINER_WORKDIR

if TYPE_CHECKING:  # pragma: no cover
    from .runner import ExecResult, SandboxPolicy

#: Obraz z narzędziami; operator może wskazać obraz projektu (FROM ten obraz
#: + zależności ocenianego repo).
IMAGE_ENV = "GATEKEEPER_CONTAINER_IMAGE"
DEFAULT_IMAGE = "gatekeeper-tools:latest"
#: `docker` albo `podman`; domyślnie pierwszy znaleziony.
ENGINE_ENV = "GATEKEEPER_CONTAINER_ENGINE"
#: Etykieta, po której nadzorca bramki usuwa jej kontenery (execution.py).
OWNER_ENV = "GATEKEEPER_CONTAINER_OWNER"
OWNER_LABEL = "gatekeeper.owner"
#: Prywatny HOME (tmpfs): `--read-only` nie pozwala go utworzyć w obrazie
#: dla dowolnego uid, a narzędzia (npm, dotnet) chcą do niego pisać.
_HOME = "/home/gatekeeper"
#: Gdzie w kontenerze widać cache NuGet hosta.
NUGET_HOST_CACHE = "/opt/nuget-host"
#: Zapas ponad limit czasu wywołania, po którym kontener kończy się sam —
#: także gdy proces bramy zginął i nikt nie wywoła `docker rm`.
SELF_DESTRUCT_GRACE_S = 30
#: Limit procesów w kontenerze — w przeciwieństwie do RLIMIT_NPROC liczy tylko
#: procesy tego kontenera, więc może być ustawiony zawsze.
PIDS_LIMIT = 1024
#: Zmienne hosta, które mają sens w kontenerze. PATH, HOME i reszta pochodzą
#: z obrazu — wartości z Windows czy z venv hosta byłyby tam bez znaczenia.
_PASSTHROUGH_ENV = ("LANG", "LC_ALL", "TZ", "CI", "NO_COLOR", "FORCE_COLOR")
_HOST_ONLY_ENV = {"PATH", "HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR", "PWD", "OLDPWD"}
#: `docker run` kończy się 127/126, gdy w obrazie nie ma programu.
_MISSING_EXECUTABLE = re.compile(
    r"executable file not found|no such file or directory|not found in \$PATH", re.IGNORECASE
)


@functools.lru_cache(maxsize=1)
def engine() -> str | None:
    """Ścieżka do `docker`/`podman` albo None."""
    wanted = os.environ.get(ENGINE_ENV)
    for name in (wanted,) if wanted else ("docker", "podman"):
        found = shutil.which(name)
        if found:
            return found
    return None


def image() -> str:
    return os.environ.get(IMAGE_ENV) or DEFAULT_IMAGE


@functools.lru_cache(maxsize=8)
def _image_present(engine_path: str, name: str) -> bool:
    try:
        probe = subprocess.run(
            [engine_path, "image", "inspect", "--format", "{{.Id}}", name],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def container_isolation_available() -> bool:
    found = engine()
    return found is not None and _image_present(found, image())


def _engine_problem(engine_path: str) -> str | None:
    """Opis, czemu demon nie odpowiada (uprawnienia do gniazda, nie działa)."""
    try:
        probe = subprocess.run(
            [engine_path, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if probe.returncode != 0 or not probe.stdout.strip():
        return (probe.stderr.strip().splitlines() or ["brak odpowiedzi serwera"])[-1]
    return None


def unavailable_reason() -> str:
    found = engine()
    if found is None:
        return (
            "brak silnika kontenerów — zainstaluj Docker albo Podman "
            f"(albo wskaż go w {ENGINE_ENV}); wykonanie bez izolacji jest zabronione"
        )
    problem = _engine_problem(found)
    if problem:
        return (
            f"silnik kontenerów nie odpowiada ({problem}); "
            "wykonanie bez izolacji jest zabronione"
        )
    return (
        f"brak obrazu {image()} — zbuduj go (`docker build -t {DEFAULT_IMAGE} "
        f"-f container/Dockerfile .`) albo wskaż obraz projektu w {IMAGE_ENV}"
    )


def describe() -> str:
    return (
        f"izolacja w kontenerze ({image()}): prywatny system plików, PID i sieć; "
        "sieć dostępna wyłącznie narzędziom żądającym jej jawnie"
    )


@dataclass(frozen=True)
class _Mount:
    host: Path
    container: str
    read_only: bool


class _PathMap:
    """Przepisywanie ścieżek host ↔ kontener.

    Dopasowanie po pełnych segmentach: `/work` nie podmienia `/workshop`.
    Na hoście Windows ścieżki bywają z `\\` albo `/` — obie postacie są
    rozpoznawane, w drugą stronę zawsze wraca postać z `/` (poprawna dla
    `Path` i bezpieczna w JSON-ie, w przeciwieństwie do `\\`).
    """

    def __init__(self, mounts: Sequence[_Mount]) -> None:
        self.mounts = sorted(mounts, key=lambda m: len(str(m.host)), reverse=True)

    def _host_forms(self, host: Path) -> list[str]:
        raw = str(host)
        forms = {raw, raw.replace("\\", "/")}
        return sorted(forms, key=len, reverse=True)

    def to_container(self, value: str) -> str:
        for mount in self.mounts:
            for form in self._host_forms(mount.host):
                value = _replace_root(value, form, mount.container, windows=True)
        return value

    def to_host(self, value: str) -> str:
        for mount in sorted(self.mounts, key=lambda m: len(m.container), reverse=True):
            value = _replace_root(
                value, mount.container, str(mount.host).replace("\\", "/"), windows=False
            )
        return value


def _replace_root(text: str, root: str, replacement: str, *, windows: bool) -> str:
    if not root:
        return text
    tail = r"(?=$|[/\\\s\"',:;=)\]}>])" if windows else r"(?=$|[/\s\"',:;=)\]}>])"
    pattern = re.compile(r"(?<![\w.\-/\\])" + re.escape(root) + tail, re.IGNORECASE if windows
                         and re.match(r"^[A-Za-z]:", root) else 0)
    converted = []
    last = 0
    for match in pattern.finditer(text):
        converted.append(text[last:match.start()])
        converted.append(replacement)
        last = match.end()
        if windows:
            # Reszta ścieżki po korzeniu: separatory Windows → `/`.
            rest = re.match(r"[^\s\"',;=)\]}>]*", text[last:])
            if rest:
                converted.append(rest.group(0).replace("\\", "/"))
                last += rest.end()
    converted.append(text[last:])
    return "".join(converted)


def _mounts(
    cwd: Path, policy: SandboxPolicy, dependencies: Sequence[Path]
) -> tuple[list[_Mount], list[str]]:
    from .runner import SandboxUnavailable

    mounts = [_Mount(cwd, CONTAINER_WORKDIR, read_only=False)]
    extra: list[str] = []
    # Baza Git i współdzielone pakiety tylko do odczytu, jak przy Bubblewrap.
    git_dir = cwd / ".git"
    if git_dir.is_dir() and not is_link(git_dir):
        extra += ["--mount", _bind(git_dir, f"{CONTAINER_WORKDIR}/.git", True)]
    declared = {p.resolve() for p in policy.read_only_paths} | {p.resolve() for p in dependencies}
    modules = cwd / "node_modules"
    if is_link(modules):
        target = modules.resolve()
        if target not in declared:
            raise SandboxUnavailable("node_modules jest niezaufanym dowiązaniem poza kopię kodu")
        extra += ["--mount", _bind(target, f"{CONTAINER_WORKDIR}/node_modules", True)]
        declared.discard(target)
    elif modules.is_dir():
        extra += ["--mount", _bind(modules, f"{CONTAINER_WORKDIR}/node_modules", True)]
    for index, path in enumerate(sorted(declared)):
        if path == cwd or path.is_relative_to(cwd):
            continue
        mounts.append(_Mount(path, f"/ro/{index}", read_only=True))
    # Cache NuGet hosta (pakiety, bez NuGet.Config i poświadczeń) — jak przy
    # Bubblewrap: restore działa offline. Obraz projektu ma własny /opt/nuget.
    nuget = Path.home() / ".nuget" / "packages"
    if nuget.is_dir():
        extra += ["--mount", _bind(nuget.resolve(), NUGET_HOST_CACHE, True)]
        extra += ["--env", f"NUGET_PACKAGES={NUGET_HOST_CACHE}"]
    for index, path in enumerate(policy.writable_paths):
        resolved = path.resolve(strict=True)
        if resolved == cwd or resolved.is_relative_to(cwd):
            continue
        mounts.append(_Mount(resolved, f"/rw/{index}", read_only=False))
    return mounts, extra


def _bind(source: Path, target: str, read_only: bool) -> str:
    """Specyfikacja `--mount` (CSV): pole z przecinkiem albo cudzysłowem
    musi być w cudzysłowie, inaczej ścieżka `a,b` rozbiłaby opcje."""

    def field(key: str, value: str) -> str:
        item = f"{key}={value}"
        if "," in item or '"' in item:
            return '"' + item.replace('"', '""') + '"'
        return item

    parts = ["type=bind", field("source", str(source)), field("target", target)]
    if read_only:
        parts.append("readonly")
    return ",".join(parts)


def _executable(argv0: str, mapping: _PathMap) -> str:
    """Program w kontenerze dla `argv[0]` z hosta.

    Interpreter Pythona (bramy albo venv) → `python` z obrazu; program spod
    zamontowanego katalogu → jego ścieżka w kontenerze; każdy inny program
    hosta → sama nazwa (szukana w PATH obrazu, bez `.exe`/`.cmd`).
    """
    mapped = mapping.to_container(argv0)
    if mapped != argv0:
        return mapped
    name = PurePosixPath(argv0.replace("\\", "/")).name
    stem = re.sub(r"\.(exe|cmd|bat|ps1)$", "", name, flags=re.IGNORECASE)
    if argv0 == sys.executable or re.fullmatch(r"python(3(\.\d+)?)?", stem):
        return "python"
    return stem


def _environment(
    env: Mapping[str, str], policy: SandboxPolicy, mapping: _PathMap
) -> dict[str, str]:
    """Zmienne dla kontenera: przekazujemy tylko to, co wnosi wywołujący.

    Wywołujący zwykle podają `dict(os.environ) + dodatki` (PYTHONPATH,
    NODE_PATH). Środowisko hosta jako całość nie ma w kontenerze sensu,
    więc idą wyłącznie dodatki, zmienne z `keep_env` i bezpieczna lista.
    """
    host = os.environ
    chosen: dict[str, str] = {}
    for name, value in env.items():
        if name in _HOST_ONLY_ENV:
            continue
        if name in policy.keep_env or name in _PASSTHROUGH_ENV or host.get(name) != value:
            chosen[name] = value
    if "PYTHONPATH" in chosen:
        parts = chosen["PYTHONPATH"].split(os.pathsep)
        chosen["PYTHONPATH"] = ":".join(mapping.to_container(p) for p in parts if p)
    if "NODE_PATH" in chosen:
        parts = chosen["NODE_PATH"].split(os.pathsep)
        chosen["NODE_PATH"] = ":".join(mapping.to_container(p) for p in parts if p)
    return {name: mapping.to_container(value) for name, value in chosen.items()}


def _user() -> str:
    if hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"  # pliki w kopii należą do operatora
    return "1000:1000"  # Docker Desktop mapuje własność montowań sam


def build_command(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    policy: SandboxPolicy,
    network: bool,
    dependencies: Sequence[Path],
    name: str,
    timeout_s: float | None = None,
) -> tuple[list[str], _PathMap]:
    found = engine()
    assert found is not None  # sprawdzone przez wywołującego
    mounts, extra = _mounts(cwd, policy, dependencies)
    mapping = _PathMap(mounts)
    command = [
        found, "run", "--rm", "--init",
        "--name", name,
        "--label", "gatekeeper=1",
        "--network", "bridge" if network else "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=2g,mode=1777",
        "--tmpfs", f"{_HOME}:rw,exec,nosuid,size=1g,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(PIDS_LIMIT),
        "--user", _user(),
        "--workdir", CONTAINER_WORKDIR,
        "--env", f"HOME={_HOME}",
        "--env", "TMPDIR=/tmp",
        "--env", f"DOTNET_CLI_HOME={_HOME}",
        "--env", "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "--env", "DOTNET_CLI_USE_MSBUILD_SERVER=0",
    ]
    owner = os.environ.get(OWNER_ENV)
    if owner:
        command += ["--label", f"{OWNER_LABEL}={owner}"]
    if policy.memory_mb:
        command += ["--memory", f"{policy.memory_mb}m", "--memory-swap", f"{policy.memory_mb}m"]
    for mount in mounts:
        command += ["--mount", _bind(mount.host, mount.container, mount.read_only)]
    command += extra
    for key, value in sorted(_environment(env, policy, mapping).items()):
        command += ["--env", f"{key}={value}"]
    command.append(image())
    if timeout_s is not None:
        # Kontener nie przeżyje bramy: `timeout` (coreutils w obrazie) zabija
        # narzędzie, nawet gdy nadzorca zginął przed `docker rm`.
        command += ["timeout", "-s", "KILL", str(int(timeout_s) + SELF_DESTRUCT_GRACE_S)]
    command.append(_executable(argv[0], mapping))
    command += [mapping.to_container(arg) for arg in argv[1:]]
    return command, mapping


def run(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    policy: SandboxPolicy,
    network: bool,
    timeout_s: float,
    dependencies: Sequence[Path],
) -> ExecResult:
    from .runner import ExecResult, ExecutableUnavailable, SandboxUnavailable

    if not container_isolation_available():
        raise SandboxUnavailable(unavailable_reason())
    name = f"gk-{uuid.uuid4().hex[:16]}"
    command, mapping = build_command(
        argv, cwd, env, policy, network, dependencies, name, timeout_s
    )
    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        remove_containers(name=name)
        proc.kill()
        stdout, stderr = proc.communicate()
    if proc.returncode in (126, 127) and _MISSING_EXECUTABLE.search(stderr or ""):
        raise ExecutableUnavailable(f"nie znaleziono programu w obrazie {image()}: {argv[0]}")
    if proc.returncode == 125:
        raise SandboxUnavailable(f"silnik kontenerów nie uruchomił narzędzia: {stderr.strip()}")
    return ExecResult(
        returncode=proc.returncode,
        stdout=mapping.to_host(stdout or ""),
        stderr=mapping.to_host(stderr or ""),
        duration_s=time.monotonic() - started,
        timed_out=timed_out,
        isolation="container",
        command=tuple(command),
    )


def remove_containers(*, name: str | None = None, owner: str | None = None) -> None:
    """Zabija i usuwa kontenery bramy — po nazwie albo po etykiecie właściciela.

    Zabicie klienta `docker run` nie zatrzymuje kontenera, więc nadzorca musi
    sprzątać po silniku jawnie (budżet czasu, anulowanie, awaria workera).
    """
    found = engine()
    if found is None:
        return
    targets: list[str] = []
    if name:
        targets.append(name)
    if owner:
        try:
            listed = subprocess.run(
                [found, "ps", "-aq", "--filter", f"label={OWNER_LABEL}={owner}"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            targets += listed.stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return
    if targets:
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [found, "rm", "-f", *targets], capture_output=True, timeout=30, check=False
            )


def project_dockerfile(repo: Path, base: str = DEFAULT_IMAGE) -> str:
    """Szablon obrazu projektu: narzędzia bramy + zależności ocenianego repo.

    Odpowiednik przygotowania venv/`node_modules`/cache NuGet przy Bubblewrap.
    Zależności instaluje operator, raz, przy budowaniu obrazu — nie bramka
    w trakcie przebiegu (instalacja wykonuje kod pakietów, np. setup.py).
    """
    lines = [
        "# Obraz projektu dla llm-code-gatekeeper (backend izolacji `container`).",
        "# Zbuduj z katalogu ocenianego repo:",
        "#   docker build -t gatekeeper-projekt:latest -f Dockerfile.gatekeeper .",
        "# i wskaż go bramie: GATEKEEPER_CONTAINER_IMAGE=gatekeeper-projekt:latest",
        f"FROM {base}",
        "USER root",
        "WORKDIR /opt/projekt",
    ]
    python_manifests = sorted(
        p.name for p in repo.glob("requirements*.txt") if p.is_file()
    )
    if python_manifests:
        lines.append(f"COPY {' '.join(python_manifests)} ./")
        lines += [f"RUN pip install -r {name}" for name in python_manifests]
    elif (repo / "pyproject.toml").is_file():
        lines += [
            "# Zależności z pyproject.toml (bez samego pakietu — jego kod przychodzi z PR-a).",
            "COPY pyproject.toml ./",
            "RUN python -c \"import tomllib, subprocess, sys; d = tomllib.load(open("
            "'pyproject.toml', 'rb')); deps = d.get('project', {}).get('dependencies', []);"
            " deps and subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])\"",
        ]
    if (repo / "package.json").is_file():
        lock = "package-lock.json" if (repo / "package-lock.json").is_file() else ""
        lines += [
            "# Pakiety npm dla Linuksa (moduły natywne z Windows tu nie zadziałają).",
            f"COPY package.json {lock} ./".replace("  ", " "),
            "RUN npm ci --no-audit --no-fund" if lock else "RUN npm install --no-audit --no-fund",
            "ENV NODE_PATH=/opt/projekt/node_modules:/usr/local/lib/node_modules",
        ]
    projects = sorted(
        str(p.relative_to(repo).as_posix())
        for pattern in ("*.csproj", "*/*.csproj", "*/*/*.csproj")
        for p in repo.glob(pattern)
        if "bin" not in p.parts and "obj" not in p.parts
    )
    if projects:
        lines.append("# Pakiety NuGet do /opt/nuget — bramka przywraca je potem bez sieci.")
        for project in projects:
            lines.append(f"COPY {project} {project}")
        for project in projects:
            lines.append(f"RUN dotnet restore {project}")
        lines.append("RUN chmod -R a+rX /opt/nuget")
    if len(lines) == 7:
        lines.append("# Nie wykryto manifestów zależności — obraz bazowy wystarczy.")
    lines += ["WORKDIR /work", "USER gatekeeper", ""]
    return "\n".join(lines)
