"""Uruchamianie narzędzi w Bubblewrap: prywatny system plików, PID i sieć.

Brak działającego Bubblewrap jest błędem. Nie ma automatycznego przejścia
na wykonanie kodu PR-a z uprawnieniami procesu bramy. Widoczne są wyłącznie
runtime, katalog roboczy i jawnie udostępnione ścieżki; HOME i /tmp są prywatne.
"""

from __future__ import annotations

import functools
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

#: Fragmenty nazw zmiennych środowiskowych, które nie mają prawa trafić do
#: uruchamianego procesu.
SECRET_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "APIKEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
)

Isolation = Literal["container", "network-namespace", "none", "filesystem", "filesystem-network"]


class SandboxUnavailable(RuntimeError):
    pass


class ExecutableUnavailable(SandboxUnavailable):
    """Brak opcjonalnego programu, odrębny od awarii granicy izolacji."""


_DEPENDENCIES: ContextVar[tuple[Path, ...]] = ContextVar("dependencies", default=())


@contextmanager
def dependency_access(paths: tuple[Path, ...]) -> Iterator[None]:
    """Grant od zaufanego nadzorcy, nigdy ze ścieżek odczytanych z kodu PR-a."""
    token = _DEPENDENCIES.set(paths)
    try:
        yield
    finally:
        _DEPENDENCIES.reset(token)


def dependency_paths(root: Path) -> tuple[Path, ...]:
    modules = root / "node_modules"
    if not modules.is_dir():
        return ()
    resolved = modules.resolve()
    if modules.is_symlink() and resolved not in _DEPENDENCIES.get():
        raise SandboxUnavailable("node_modules jest niezaufanym dowiązaniem poza kopię kodu")
    return (resolved,)


@dataclass(frozen=True)
class SandboxPolicy:
    """Czego wolno uruchamianemu procesowi."""

    network: bool = False
    timeout_s: float = 300.0
    memory_mb: int | None = 4096
    #: Świadomie `None`: RLIMIT_NPROC liczy procesy całego użytkownika, więc
    #: niski limit potrafi wywrócić procesy niezwiązane z bramą.
    max_processes: int | None = None
    keep_env: tuple[str, ...] = ()
    #: Zachowane dla zgodności API; izolacja jest teraz obowiązkowa.
    require_isolation: bool = True
    read_only_paths: tuple[Path, ...] = ()
    writable_paths: tuple[Path, ...] = ()


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    isolation: Isolation = "none"
    command: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, limit: int = 500) -> str:
        return (self.stderr or self.stdout)[-limit:].strip()


@dataclass
class Sandbox:
    """Uruchamianie procesów zgodnie z polityką izolacji."""

    policy: SandboxPolicy = field(default_factory=SandboxPolicy)

    def run(
        self,
        command: Sequence[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        network: bool | None = None,
    ) -> ExecResult:
        want_network = self.policy.network if network is None else network
        timeout = timeout_s if timeout_s is not None else self.policy.timeout_s
        environment = scrub_environment(
            dict(os.environ if env is None else env), self.policy.keep_env
        )

        argv = list(command)
        if not argv:
            raise ValueError("puste polecenie")
        executable = shutil.which(argv[0], path=environment.get("PATH", os.defpath))
        if executable is None:
            raise ExecutableUnavailable(f"nie znaleziono programu: {argv[0]}")
        if not filesystem_isolation_available():
            raise SandboxUnavailable(
                "brak izolacji Bubblewrap — zainstaluj bubblewrap i udostępnij "
                "przestrzenie nazw użytkownika; wykonanie bez izolacji jest zabronione"
            )
        binary = Path(executable)
        # Python rozpoznaje venv po ścieżce uruchomienia, pozostałe
        # dowiązania rozwiązujemy, aby nie udostępniać katalogów menedżera wersji.
        argv[0] = str(
            binary.parent.resolve() / binary.name
            if (binary.parent.parent / "pyvenv.cfg").is_file()
            else binary.resolve()
        )
        environment["PATH"] = os.pathsep.join(
            str(Path(p).resolve())
            for p in environment.get("PATH", os.defpath).split(os.pathsep)
            if p
        )
        argv = _wrap_filesystem(argv, Path(cwd).resolve(), environment, self.policy, want_network)
        isolation: Isolation = "filesystem" if want_network else "filesystem-network"

        started = time.monotonic()
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                cwd=str(cwd),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # własna grupa procesów — da się ubić w całości
                preexec_fn=self._limits(),  # noqa: PLW1509
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailable(f"nie znaleziono programu: {argv[0]}") from exc

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            stdout, stderr = proc.communicate()
        if proc.returncode != 0 and stderr.startswith("bwrap:"):
            raise SandboxUnavailable(f"Bubblewrap nie uruchomił narzędzia: {stderr.strip()}")
        return ExecResult(
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            isolation=isolation,
            command=tuple(argv),
        )

    def _limits(self) -> Callable[[], None]:
        memory_mb = self.policy.memory_mb
        max_processes = self.policy.max_processes

        def apply() -> None:  # pragma: no cover - wykonuje się w procesie potomnym
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if memory_mb:
                limit = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            if max_processes:
                resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))

        return apply


def _runtime_paths(executable: str) -> set[Path]:
    paths = {Path(sys.prefix), Path(sys.base_prefix)}
    for name in (executable, "node", "dotnet"):
        resolved = shutil.which(name)
        if resolved is None:
            continue
        binary = Path(resolved).resolve()
        paths.add(binary)
        if binary.name in {"node", "dotnet"}:
            paths.add(binary.parent.parent if binary.parent.name == "bin" else binary.parent)
        for parent in binary.parents:
            if (parent / "pyvenv.cfg").is_file() or parent.name == "node_modules":
                paths.add(parent)
                break
        if (binary.parent / ".store").is_dir():
            paths.add(binary.parent / ".store")
    return {p for p in paths if p != Path("/") and p.exists()}


def _wrap_filesystem(
    argv: list[str],
    cwd: Path,
    environment: dict[str, str],
    policy: SandboxPolicy,
    network: bool,
) -> list[str]:
    command = [
        "bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    if not network:
        command.append("--unshare-net")
    # Nie montujemy / ani HOME hosta. Szczególnie /proc musi należeć do
    # nowej przestrzeni PID, inaczej /proc/<pid>/root omijałoby whitelistę.
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(path).exists():
            command.extend(("--ro-bind", path, path))
    for path in (
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ssl/certs",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/localtime",
    ):
        if Path(path).exists():
            command.extend(("--ro-bind", path, path))
    command.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"))
    declared = {p.resolve() for p in policy.read_only_paths} | set(_DEPENDENCIES.get())
    modules = cwd / "node_modules"
    if modules.is_symlink() and modules.resolve() not in declared:
        raise SandboxUnavailable("node_modules jest niezaufanym dowiązaniem poza kopię kodu")
    readable = _runtime_paths(argv[0]) | declared
    if modules.is_dir():
        readable.add(modules.resolve())
    # Cache zawiera pakiety, nie NuGet.Config ani poświadczenia użytkownika.
    nuget = Path.home() / ".nuget" / "packages"
    if nuget.is_dir():
        readable.add(nuget)
        environment["NUGET_PACKAGES"] = str(nuget)
    command.extend(("--bind", str(cwd), str(cwd)))
    system_roots = [Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64")]
    for readable_path in sorted(readable):
        if any(
            readable_path != parent and readable_path.is_relative_to(parent)
            for parent in [*system_roots, *readable]
            if parent.is_dir()
        ):
            continue
        if readable_path in system_roots:
            continue
        command.extend(("--ro-bind", str(readable_path), str(readable_path)))
    for writable_path in policy.writable_paths:
        resolved = writable_path.resolve(strict=True)
        command.extend(("--bind", str(resolved), str(resolved)))
    # Kod może pisać artefakty budowania, ale nie zmieniać bazy Git ani
    # współdzielonych pakietów. Dowiązania poza whitelistę pozostają niewidoczne.
    for protected in (cwd / ".git", modules):
        if protected.exists() and not protected.is_symlink():
            command.extend(("--ro-bind", str(protected.resolve()), str(protected)))
    command.extend(("--dir", "/tmp/gatekeeper-home", "--chdir", str(cwd)))
    environment.update(HOME="/tmp/gatekeeper-home", TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp")
    dotnet = shutil.which("dotnet")
    if dotnet:
        environment.setdefault("DOTNET_ROOT", str(Path(dotnet).resolve().parent))
    environment["DOTNET_CLI_HOME"] = "/tmp/gatekeeper-home"
    environment.pop("SSH_AUTH_SOCK", None)
    environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
    command.extend(("--", *argv))
    return command


@functools.lru_cache(maxsize=1)
def filesystem_isolation_available() -> bool:
    if sys.platform != "linux" or shutil.which("bwrap") is None:
        return False
    try:
        probe = subprocess.run(
            [
                "bwrap",
                "--unshare-user",
                "--unshare-pid",
                "--unshare-net",
                "--ro-bind",
                "/",
                "/",
                "--",
                "/bin/true",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def network_isolation_available() -> bool:
    """Zgodność API: bez Bubblewrap nie uruchamiamy też testów sieciowych."""
    return filesystem_isolation_available()


def scrub_environment(env: dict[str, str], keep: Sequence[str] = ()) -> dict[str, str]:
    """Usuwa zmienne wyglądające na poświadczenia; `keep` przywraca wskazane."""
    keep_set = set(keep)
    return {
        name: value
        for name, value in env.items()
        if name in keep_set or not any(marker in name.upper() for marker in SECRET_ENV_MARKERS)
    }


def describe_isolation() -> str:
    """Jednozdaniowy opis do raportu — brama ma mówić, czego *nie* gwarantuje."""
    if filesystem_isolation_available():
        return (
            "izolacja Bubblewrap: prywatny system plików i PID; sieć testów odcięta, "
            "sieć dostępna wyłącznie narzędziom żądającym jej jawnie"
        )
    return "BRAK izolacji Bubblewrap — wykonanie narzędzi i testów jest zablokowane"


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover
        proc.kill()
