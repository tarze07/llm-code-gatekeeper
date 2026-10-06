"""Procesy bramek, termin zakończenia i prywatne kopie kodu (Linux)."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from collections import deque
from contextlib import ExitStack, suppress
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection, wait
from multiprocessing.process import BaseProcess
from pathlib import Path

from ..gates import Gate
from .change import ChangeContext
from .finding import GateResult
from .policy import Policy
from .progress import RunCancelled, RunControl
from .runner import dependency_access, dependency_paths


def _worker(
    gate: Gate, change: ChangeContext, output: Connection, dependencies: tuple[Path, ...]
) -> None:
    os.setsid()
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


@dataclass
class Running:
    gate: Gate
    process: BaseProcess
    output: Connection
    deadline: float
    resources: ExitStack

    def close(self) -> None:
        # Zabij także bezpośrednie narzędzia pozostawione przez plugin.
        # Sandbox używa własnego PID namespace i --die-with-parent.
        if self.process.pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
        if self.process.is_alive():
            self.process.kill()
        self.process.join()
        self.process.close()
        self.output.close()
        self.resources.close()


def run_wave(
    gates: list[Gate],
    change: ChangeContext,
    policy: Policy,
    max_workers: int,
    control: RunControl | None = None,
) -> list[GateResult]:
    if max_workers < 1:
        raise ValueError("max_workers musi być dodatnie")
    # fork zachowuje zainstalowane pluginy i konfigurację bez wymogu
    # serializowania obiektów dostawców. Nadzorca nie uruchamia wątków.
    context = mp.get_context("fork")
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
                        (root / "node_modules").symlink_to(dependencies[0], True)
                    isolated = replace(change, repo=root, scratch_dir=root.parent)
                    receive, send = context.Pipe(duplex=False)
                    process = context.Process(
                        target=_worker, args=(gate, isolated, send, dependencies)
                    )
                    process.start()
                    send.close()
                    active.append(
                        Running(gate, process, receive, time.monotonic() + gate.budget_s, resources)
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
