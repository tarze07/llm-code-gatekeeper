"""Windows: Job Object zabija narzędzia bramki, zanim kopia kodu zniknie.

Odpowiednik test_execution_cleanup.py (Linux, /proc). Narzędzie zapisuje swój
PID do pliku poza kopią — po powrocie `run_gates` proces ma nie żyć, a kopia
ma być usunięta. Także proces w nowej grupie i bez konsoli (odpowiednik
`start_new_session`) i po śmierci samego nadzorcy.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path

import pytest

from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.finding import GateResult
from gatekeeper_core.core.orchestrator import run_gates
from gatekeeper_core.core.policy import Policy
from gatekeeper_core.gates import Gate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects tylko na Windows")

_SLEEPER = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(300)"


class Spawner(Gate):
    """Uruchamia długi proces i czeka dłużej niż budżet bramki."""

    def __init__(self, gate_id: str, mode: str, pid_file: str, budget_s: float = 3.0) -> None:
        super().__init__({})
        self.id = gate_id
        self.name = gate_id
        self.mode = mode
        self.pid_file = pid_file
        self.budget_s = budget_s

    def run(self, change) -> GateResult:
        command = [sys.executable, "-c", _SLEEPER, self.pid_file]
        if self.mode == "detached":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(command, cwd=change.repo, creationflags=flags)  # noqa: S603
            time.sleep(300)
        else:
            subprocess.run(command, cwd=change.repo, check=False)  # noqa: S603
        return GateResult(gate=self.id, status="pass")


def _change(repo, scratch: Path) -> ChangeContext:
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "x = 1\n")
    repo.commit("zmiana")
    scratch.mkdir()
    return replace(ChangeContext.from_git(repo.path, "main", "HEAD"), scratch_dir=scratch)


def _wait_for_pid(path: Path, timeout_s: float = 60.0) -> int:
    deadline = time.monotonic() + timeout_s
    while not (path.exists() and path.read_text().strip()):
        assert time.monotonic() < deadline, f"narzędzie nie wystartowało: brak {path}"
        time.sleep(0.05)
    return int(path.read_text())


def _gone(pid: int, timeout_s: float = 15.0) -> bool:
    from gatekeeper_core.core.winjob import process_alive

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("mode", ["plain", "detached"])
def test_po_budzecie_narzedzie_ginie_a_kopia_znika(repo, tmp_path, mode):
    scratch = tmp_path / "scratch"
    pid_file = tmp_path / f"{mode}.pid"
    change = _change(repo, scratch)

    result = run_gates(
        change, Policy.from_dict({"version": 1}),
        gates=[Spawner("G1.deps", mode, str(pid_file), budget_s=5.0)],
    )

    gate = next(g for g in result.gate_results if g.gate == "G1.deps")
    assert gate.status == "error"
    assert "budżet" in gate.message
    assert _gone(_wait_for_pid(pid_file))
    assert list(scratch.iterdir()) == []


def test_smierc_nadzorcy_zabija_narzedzia_bramki(repo, tmp_path):
    scratch = tmp_path / "scratch"
    pid_file = tmp_path / "orphan.pid"
    change = _change(repo, scratch)
    script = tmp_path / "nadzorca.py"
    script.write_text(textwrap.dedent(f"""
        from dataclasses import replace
        from pathlib import Path
        from gatekeeper_core.core.change import ChangeContext
        from gatekeeper_core.core.orchestrator import run_gates
        from gatekeeper_core.core.policy import Policy
        from tests.test_execution_windows import Spawner

        if __name__ == "__main__":
            change = replace(
                ChangeContext.from_git(Path({str(repo.path)!r}), "main", "HEAD"),
                scratch_dir=Path({str(scratch)!r}),
            )
            run_gates(change, Policy.from_dict({{"version": 1}}),
                      gates=[Spawner("G1.deps", "detached", {str(pid_file)!r}, budget_s=600)])
    """), encoding="utf-8")
    del change

    supervisor = subprocess.Popen(  # noqa: S603
        [sys.executable, str(script)], cwd=Path(__file__).resolve().parents[1]
    )
    try:
        pid = _wait_for_pid(pid_file)
        supervisor.kill()  # TerminateProcess — bez szansy na sprzątanie w Pythonie
        supervisor.wait(30)
        assert _gone(pid), "narzędzie bramki przeżyło nadzorcę"
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
