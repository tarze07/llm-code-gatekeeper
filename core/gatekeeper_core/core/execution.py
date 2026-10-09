"""Procesy bramek, termin zakończenia i prywatne kopie kodu.

Linux/POSIX: `fork`, `setsid`, `PR_SET_PDEATHSIG` i spis potomków z `/proc`.
Windows: `spawn` i Job Object (`winjob.py`) — członkostwo w jobie dziedziczą
wszystkie procesy bramki, a job ginie razem z nadzorcą.
"""

from __future__ import annotations

import ctypes
import multiprocessing as mp
import os
import signal
import sys
import time
import uuid
from collections import deque
from contextlib import ExitStack, suppress
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection, wait
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path

from ..gates import Gate
from . import container
from .change import ChangeContext
from .finding import GateResult
from .fsutil import link_dir
from .policy import Policy
from .progress import RunCancelled, RunControl
from .runner import backend, dependency_access, dependency_paths
from .winjob import Job

_WINDOWS = sys.platform == "win32"
#: Ile worker na Windows czeka na potwierdzenie, że jest już w jobie nadzorcy.
JOB_HANDSHAKE_S = 30.0

#: Ile nadzorca czeka, aż zabite procesy bramki naprawdę znikną, zanim usunie
#: jej kopię kodu. SIGKILL nie da się zignorować, więc w praktyce to milisekundy;
#: dłużej trwa tylko proces zawieszony w jądrze (stan D).
REAP_GRACE_S = 5.0

_PR_SET_PDEATHSIG = 1


def _die_with_supervisor() -> None:
    """Worker ginie razem z procesem nadzorującym.

    Workery robią `setsid()`, więc SIGKILL grupy procesu przebiegu (eskalacja
    anulowania w panelu) by ich nie dosięgnął — dobiegałyby w tle z otwartą
    kopią kodu. Sandbox ma `--die-with-parent`, więc śmierć workera zabija też
    narzędzia. Brak tej gwarancji to awaria bramki, nie ciche „i tak policzymy".
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) nie powiódł się")
    parent = mp.parent_process()
    if parent is not None and os.getppid() != parent.pid:
        # Nadzorca zmarł, zanim sygnał został ustawiony.
        os._exit(1)


def _worker(
    gate: Gate,
    change: ChangeContext,
    output: Connection,
    dependencies: tuple[Path, ...],
    owner: str,
    in_job: Event | None = None,
) -> None:
    if in_job is not None:
        # Windows: nie uruchamiamy niczego, zanim nadzorca nie przypisze nas
        # do joba — inaczej narzędzie zdążyłoby wymknąć się spod `close()`.
        if not in_job.wait(JOB_HANDSHAKE_S):
            output.send(GateResult(
                gate=gate.id, status="error",
                message="proces bramki nie dostał przydziału do Job Object nadzorcy",
            ))
            output.close()
            return
    else:
        os.setsid()
        _die_with_supervisor()
    # Kontenery tej bramki dostają etykietę — nadzorca usuwa je w `close()`,
    # bo zabicie klienta `docker run` nie zatrzymuje kontenera.
    os.environ[container.OWNER_ENV] = owner
    started = time.monotonic()
    try:
        with dependency_access(dependencies):
            result = gate.run(change)
    except Exception as exc:  # noqa: BLE001 — awaria pluginu jest wynikiem bramki
        result = GateResult(gate=gate.id, status="error", message=f"wyjątek w bramce: {exc}")
    result.duration_s = time.monotonic() - started
    if result.duration_s > gate.budget_s:
        result.status = "error"
        result.message = f"przekroczony budżet czasowy ({gate.budget_s:g}s)"
    try:
        output.send(result)
    finally:
        output.close()


@dataclass(frozen=True)
class _ProcStat:
    state: str
    ppid: int
    pgrp: int
    #: Czas startu w taktach zegara — odróżnia proces od późniejszego
    #: właściciela tego samego PID.
    start: int


def _proc_stat(pid: int) -> _ProcStat | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Nazwa programu w nawiasach może zawierać spacje i nawiasy.
    fields = raw.rsplit(")", 1)[1].split()
    return _ProcStat(fields[0], int(fields[1]), int(fields[2]), int(fields[19]))


def _descendants(root: int) -> dict[int, _ProcStat]:
    """Wszystkie procesy potomne `root` wg /proc — także te w innej sesji.

    Grupa procesów workera nie wystarcza: narzędzia z `start_new_session`
    (Sandbox, ale też plugin, który sam robi `setsid`) są poza nią.
    Działa, dopóki worker żyje: w chwili jego śmierci (już nie przy
    pogrzebaniu zombie) potomkowie są przepinani do init albo subreapera
    i znikają z tego drzewa. Dlatego `close()` najpierw zamraża workera.
    """
    children: dict[int, list[int]] = {}
    stats: dict[int, _ProcStat] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        stat = _proc_stat(int(entry.name))
        if stat is None:
            continue
        stats[int(entry.name)] = stat
        children.setdefault(stat.ppid, []).append(int(entry.name))
    found: dict[int, _ProcStat] = {}
    queue = list(children.get(root, ()))
    while queue:
        pid = queue.pop()
        if pid in found or pid == root:
            continue
        found[pid] = stats[pid]
        queue.extend(children.get(pid, ()))
    return found


def _still_running(pid: int, seen: _ProcStat) -> bool:
    stat = _proc_stat(pid)
    # Zombie nie trzyma już plików ani katalogu roboczego.
    return stat is not None and stat.start == seen.start and stat.state not in ("Z", "X")


@dataclass
class Running:
    gate: Gate
    process: BaseProcess
    output: Connection
    deadline: float
    resources: ExitStack
    owner: str = ""
    job: Job | None = None

    def close(self) -> None:
        """Zabija bramkę i *wszystkich* jej potomków, potem sprząta kopię.

        Ta sama ścieżka obsługuje wynik, przekroczony budżet i anulowanie.
        Kolejność ma znaczenie: kopia kodu znika dopiero, gdy nic z niej już
        nie korzysta — inaczej narzędzie po timeoucie dalej pisałoby
        (lub trzymało otwarte pliki) w katalogu, którego już nie ma.
        """
        if self.job is not None:
            self._close_job()
            return
        tree: dict[int, _ProcStat] = {}
        pid = self.process.pid
        if pid is not None:
            # Zamrożenie grupy workera: plugin nie zdąży uruchomić niczego
            # nowego między spisem potomków a ich zabiciem.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGSTOP)
            tree = _descendants(pid)
            for child, stat in tree.items():
                if not _still_running(child, stat):
                    continue  # PID mógł już przejść na inny proces
                with suppress(ProcessLookupError, PermissionError):
                    # Grupa założona przez potomka obejmuje też to, co zdążył
                    # uruchomić po spisie.
                    if stat.pgrp == child:
                        os.killpg(child, signal.SIGKILL)
                    os.kill(child, signal.SIGKILL)
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
        if self.process.is_alive():
            self.process.kill()
        self.process.join()
        self.process.close()
        self.output.close()
        deadline = time.monotonic() + REAP_GRACE_S
        while time.monotonic() < deadline and any(
            _still_running(child, stat) for child, stat in tree.items()
        ):
            time.sleep(0.02)
        # Po karencji sprzątamy mimo wszystko: proces po SIGKILL nie wykona już
        # żadnego kodu, a pozostawiona kopia byłaby trwałym wyciekiem dysku.
        if self.owner and backend() == "container":
            container.remove_containers(owner=self.owner)
        self.resources.close()

    def _close_job(self) -> None:
        """Windows: job zabija całe drzewo, a `terminate` czeka, aż procesy
        znikną — dopiero wtedy pliki kopii są zwolnione do usunięcia."""
        assert self.job is not None
        try:
            self.job.terminate(REAP_GRACE_S)
            if self.process.is_alive():
                self.process.kill()
            self.process.join()
            self.process.close()
            self.output.close()
            if self.owner and backend() == "container":
                container.remove_containers(owner=self.owner)
            self.resources.close()
        finally:
            self.job.close()


def run_wave(
    gates: list[Gate],
    change: ChangeContext,
    policy: Policy,
    max_workers: int,
    control: RunControl | None = None,
) -> list[GateResult]:
    if max_workers < 1:
        raise ValueError("max_workers musi być dodatnie")
    # POSIX: fork zachowuje zainstalowane pluginy i konfigurację bez wymogu
    # serializowania obiektów dostawców. Nadzorca nie uruchamia wątków.
    # Windows nie ma fork: spawn, a bramka i zmiana muszą być picklowalne.
    context = mp.get_context("spawn") if _WINDOWS else mp.get_context("fork")
    pending = deque(gates)
    active: list[Running] = []
    results: list[GateResult] = []
    try:
        while pending or active:
            while pending and len(active) < max_workers:
                gate = pending.popleft()
                resources = ExitStack()
                try:
                    root = resources.enter_context(change.worktree_at(change.head_sha))
                    dependencies = dependency_paths(change.repo)
                    if dependencies and not (root / "node_modules").exists():
                        link_dir(root / "node_modules", dependencies[0])
                    isolated = replace(change, repo=root, scratch_dir=root.parent)
                    receive, send = context.Pipe(duplex=False)
                    owner = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
                    job = Job() if _WINDOWS else None
                    if job is not None:
                        resources.callback(job.close)
                    in_job = context.Event() if job is not None else None
                    process = context.Process(
                        target=_worker,
                        args=(gate, isolated, send, dependencies, owner, in_job),
                    )
                    process.start()
                    send.close()
                    if job is not None and in_job is not None:
                        assert process.pid is not None
                        try:
                            job.assign(process.pid)
                        except OSError:
                            process.kill()
                            process.join()
                            raise
                        in_job.set()
                    active.append(
                        Running(
                            gate,
                            process,
                            receive,
                            time.monotonic() + gate.budget_s,
                            resources,
                            owner,
                            job,
                        )
                    )
                    if control is not None:
                        control.emit("gate_started", gate=gate.id, message=gate.name or gate.id)
                except Exception as exc:  # noqa: BLE001
                    resources.close()
                    results.append(
                        GateResult(
                            gate=gate.id,
                            status="error",
                            message=f"przygotowanie bramki: {exc}",
                            warn_only=policy.is_warn_only(gate.id),
                        )
                    )
            if not active:
                continue
            delay = max(0.0, min(item.deadline for item in active) - time.monotonic())
            if control is not None:
                # Bez ograniczenia oczekiwania żądanie anulowania czekałoby na
                # najbliższy budżet czasowy bramki, czyli nawet kilka minut.
                delay = min(delay, control.poll_interval_s)
                if control.is_cancelled():
                    raise RunCancelled("przebieg anulowany na żądanie operatora")
            ready = wait([item.output for item in active], timeout=delay)
            for item in active[:]:
                result = None
                if item.output in ready:
                    try:
                        result = item.output.recv()
                    except EOFError:
                        result = GateResult(
                            gate=item.gate.id,
                            status="error",
                            message="proces bramki zakończył się bez wyniku",
                        )
                elif time.monotonic() >= item.deadline:
                    result = GateResult(
                        gate=item.gate.id,
                        status="error",
                        duration_s=item.gate.budget_s,
                        message=f"przekroczony budżet czasowy ({item.gate.budget_s:g}s)",
                    )
                if result is not None:
                    result.warn_only = policy.is_warn_only(item.gate.id)
                    results.append(result)
                    item.close()
                    active.remove(item)
                    if control is not None:
                        control.completed += 1
                        control.emit(
                            "gate_finished",
                            gate=result.gate,
                            status=result.status,
                            message=result.message,
                        )
    finally:
        # Zamknięcie ubija *grupę procesów* każdej bramki, nie tylko jej proces
        # nadrzędny: workery robią `setsid()`, więc zabicie samego rodzica
        # zostawiłoby narzędzia w tle (PLAN-WEB-UI.md §5).
        for item in active:
            item.close()
    return results
