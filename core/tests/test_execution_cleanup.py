"""Przekroczony budżet zabija narzędzia bramki i dopiero potem usuwa jej kopię.

Bramka działa w osobnym procesie, ale narzędzia, które uruchamia, bywają poza
jego grupą procesów (Sandbox i każdy `start_new_session`). Testy pilnują, żeby
po powrocie `run_gates` nic z nich nie dobiegało w tle na usuniętej kopii.
Czasy są celowo hojne: sprawdzamy „zniknęło w ciągu kilku sekund", a nie
„w ciągu 50 ms".
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.finding import GateResult
from gatekeeper_core.core.orchestrator import run_gates
from gatekeeper_core.core.policy import Policy
from gatekeeper_core.core.runner import Sandbox, filesystem_isolation_available
from gatekeeper_core.gates import Gate

# Spis procesów z /proc, sygnały i `sleep` — mechanizm linuksowy. Odpowiednik
# na Windows (Job Object): test_execution_windows.py.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="mechanizm POSIX (/proc)")


@pytest.fixture(autouse=True)
def _backend_bwrap(monkeypatch):
    """Te testy sprawdzają Bubblewrap; backend kontenerowy ma własne
    (`test_container_backend.py`, `test_container_integration.py`)."""
    monkeypatch.setenv("GATEKEEPER_SANDBOX", "bwrap")


CORE_ROOT = Path(__file__).resolve().parents[1]


def _marker() -> str:
    """Unikalny argument `sleep`, po którym znajdziemy proces w /proc."""
    return f"300.{uuid.uuid4().int % 10**9:09d}"


def _processes_with(marker: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if marker.encode() in cmdline and state not in ("Z", "X"):
            found.append(int(entry.name))
    return found


def _wait_until_gone(marker: str, timeout_s: float = 15.0) -> list[int]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        alive = _processes_with(marker)
        if not alive:
            return []
        time.sleep(0.1)
    return _processes_with(marker)


def _wait_for(path: Path, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        assert time.monotonic() < deadline, f"bramka nie wystartowała: brak {path}"
        time.sleep(0.05)


class Spawner(Gate):
    """Bramka uruchamia długie `sleep` i czeka na nie dłużej niż jej budżet."""

    def __init__(self, gate_id: str, mode: str, marker: str, budget_s: float = 1.0) -> None:
        super().__init__({})
        self.id = gate_id
        self.name = gate_id
        self.mode = mode
        self.marker = marker
        self.budget_s = budget_s

    def run(self, change) -> GateResult:
        command = ["sleep", self.marker]
        if self.mode == "sandbox":
            Sandbox().run(command, cwd=change.repo, timeout_s=600)
        elif self.mode == "detached":
            # Plugin, który sam wychodzi z grupy procesów workera.
            subprocess.Popen(command, cwd=change.repo, start_new_session=True)  # noqa: S603
            time.sleep(600)
        else:
            subprocess.run(command, cwd=change.repo, check=False)  # noqa: S603
        return GateResult(gate=self.id, status="pass")


def _change(repo, scratch: Path) -> ChangeContext:
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "x = 1\n")
    repo.commit("zmiana")
    scratch.mkdir()
    return replace(ChangeContext.from_git(repo.path, "main", "HEAD"), scratch_dir=scratch)


def _policy() -> Policy:
    return Policy.from_dict({"version": 1})


@pytest.mark.parametrize(
    "mode",
    [
        "plain",
        "detached",
        pytest.param(
            "sandbox",
            marks=pytest.mark.skipif(
                not filesystem_isolation_available(), reason="brak Bubblewrap"
            ),
        ),
    ],
)
def test_po_budzecie_narzedzie_bramki_ginie_a_kopia_znika(repo, tmp_path, mode):
    marker = _marker()
    scratch = tmp_path / "scratch"
    change = _change(repo, scratch)

    try:
        result = run_gates(
            change, _policy(), gates=[Spawner("G1.deps", mode, marker, budget_s=1.0)]
        )

        gate = next(g for g in result.gate_results if g.gate == "G1.deps")
        assert gate.status == "error"
        assert "budżet" in gate.message
        assert _wait_until_gone(marker) == []
        # Kopia znika razem z bramką, nie zostaje na dysku.
        assert list(scratch.iterdir()) == []
    finally:
        for pid in _processes_with(marker):
            os.kill(pid, signal.SIGKILL)


def test_kopia_znika_dopiero_gdy_narzedzia_nie_dzialaja(repo, tmp_path):
    """Kolejność sprzątania: najpierw procesy, potem katalog.

    Zaraz po powrocie `run_gates` — bez czekania — żaden proces bramki nie
    może już działać, bo kopia została usunięta tylko raz, na końcu.
    """
    marker = _marker()
    scratch = tmp_path / "scratch"
    change = _change(repo, scratch)

    try:
        run_gates(change, _policy(), gates=[Spawner("G1.deps", "detached", marker, 1.0)])

        assert list(scratch.iterdir()) == []
        assert _processes_with(marker) == []
    finally:
        for pid in _processes_with(marker):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(not filesystem_isolation_available(), reason="brak Bubblewrap")
def test_smierc_procesu_nadzorujacego_zabija_bramke_i_sandbox(repo, tmp_path):
    """Eskalacja anulowania w panelu: SIGKILL procesu przebiegu.

    Workery bramek mają własną sesję, więc SIGKILL grupy przebiegu ich nie
    dosięga — muszą zginąć same (PDEATHSIG), a Sandbox razem z nimi
    (`--die-with-parent`).
    """
    marker = _marker()
    started = tmp_path / "started"
    scratch = tmp_path / "scratch"
    _change(repo, scratch)
    script = tmp_path / "supervisor.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from dataclasses import replace
            from pathlib import Path
            from gatekeeper_core.core.change import ChangeContext
            from gatekeeper_core.core.finding import GateResult
            from gatekeeper_core.core.orchestrator import run_gates
            from gatekeeper_core.core.policy import Policy
            from gatekeeper_core.core.runner import Sandbox
            from gatekeeper_core.gates import Gate

            class Wolna(Gate):
                id = "G1.deps"
                name = "G1.deps"
                budget_s = 600.0

                def run(self, change):
                    Path({str(started)!r}).write_text("ok")
                    Sandbox().run(["sleep", {marker!r}], cwd=change.repo, timeout_s=600)
                    return GateResult(gate=self.id, status="pass")

            change = replace(
                ChangeContext.from_git(Path({str(repo.path)!r}), "main", "HEAD"),
                scratch_dir=Path({str(scratch)!r}),
            )
            run_gates(change, Policy.from_dict({{"version": 1}}), gates=[Wolna({{}})])
            """
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(CORE_ROOT), *sys.path])}
    proc = subprocess.Popen([sys.executable, str(script)], env=env)  # noqa: S603
    try:
        _wait_for(started)
        deadline = time.monotonic() + 30
        while not _processes_with(marker):
            assert time.monotonic() < deadline, "narzędzie bramki nie wystartowało"
            time.sleep(0.05)
        proc.kill()
        proc.wait(timeout=10)

        assert _wait_until_gone(marker) == []
    finally:
        if proc.poll() is None:
            proc.kill()
        for pid in _processes_with(marker):
            os.kill(pid, signal.SIGKILL)
