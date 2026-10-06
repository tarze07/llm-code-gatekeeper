"""Wspólna usługa uruchamiania + postęp i anulowanie (PLAN-WEB-UI.md §5).

Testy pilnują trzech rzeczy, które łatwo zepsuć przy pierwszej poprawce:
polityka jest lintowana *przed* dotknięciem repozytorium, snapshot mapy
zakresu faktycznie dociera do bramki, a anulowanie kończy przebieg bez
decyzji zamiast zwracać fałszywy PASS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gatekeeper_core.core.finding import GateResult
from gatekeeper_core.core.orchestrator import run_gates
from gatekeeper_core.core.policy import Policy
from gatekeeper_core.core.progress import ProgressEvent, RunCancelled, RunControl
from gatekeeper_core.core.service import (
    PreparationError,
    RunRequest,
    load_policy,
    prepare,
)
from gatekeeper_core.gates import Gate

POLICY_OK = "version: 1\nblocking:\n  - secrets.found_in_diff\n"


def write_policy(tmp_path: Path, text: str = POLICY_OK) -> Path:
    path = tmp_path / "gates.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_literowka_w_fakcie_jest_odrzucona_przed_analiza_repo(tmp_path):
    policy = write_policy(tmp_path, "version: 1\nblocking:\n  - secrets.found_in_dif\n")
    request = RunRequest(repo=tmp_path / "nie-ma-takiego-repo", base="main", policy_path=policy)

    with pytest.raises(PreparationError, match="secrets.found_in_dif"):
        prepare(request)


def test_nieznana_bramka_jest_bledem_przygotowania(tmp_path, repo):
    request = RunRequest(
        repo=repo.path, base="main", policy_path=write_policy(tmp_path), gates=("G3.secret",)
    )
    with pytest.raises(PreparationError, match="G3.secret"):
        prepare(request)


def test_zly_zakres_git_jest_bledem_przygotowania(tmp_path, repo):
    request = RunRequest(repo=repo.path, base="nie-ma-galezi", policy_path=write_policy(tmp_path))
    with pytest.raises(PreparationError, match="zakres zmiany"):
        prepare(request)


def test_snapshot_mapy_zakresu_trafia_do_bramki(tmp_path):
    """Zapisanie mapy w bazie panelu nie wystarcza — bramka musi ją dostać."""
    scope_map = tmp_path / "scope_map.yaml"
    scope_map.write_text("components:\n  AUTH:\n    - 'src/**'\n", encoding="utf-8")
    request = RunRequest(
        repo=tmp_path, base="main", policy_path=write_policy(tmp_path), scope_map_path=scope_map
    )

    policy = load_policy(request)

    assert policy.gate_config("G0.scope")["scope_map_path"] == str(scope_map.resolve())


def test_bez_snapshotu_polityka_zostaje_nietknieta(tmp_path):
    policy = load_policy(RunRequest(repo=tmp_path, base="main", policy_path=write_policy(tmp_path)))
    assert "scope_map_path" not in policy.gate_config("G0.scope")


# ---------------------------------------------------------------- postęp


class CichaBramka(Gate):
    id = "T.cicha"
    name = "bramka testowa"
    budget_s = 10.0

    def run(self, change):
        return GateResult(gate=self.id, status="pass", message="ok")


class DrugaBramka(CichaBramka):
    id = "T.druga"


def test_postep_liczy_ukonczone_kontrole(repo):
    events: list[ProgressEvent] = []
    control = RunControl(observer=events.append)
    change = _change(repo)

    run_gates(change, Policy(), gates=[CichaBramka(), DrugaBramka()], control=control)

    kinds = [e.kind for e in events]
    assert kinds[0] == "plan"
    assert events[0].total == 2
    finished = [e for e in events if e.kind == "gate_finished"]
    assert {e.gate for e in finished} == {"T.cicha", "T.druga"}
    # „Ukończono N z M kontroli" — a nie procent pozostałego czasu.
    assert finished[-1].completed == 2
    assert finished[-1].total == 2


def test_pominiete_bramki_tez_domykaja_licznik(repo):
    """Inaczej pasek postępu zatrzymuje się na 9/11 i wygląda jak zawieszenie."""
    repo.checkout("docs", create=True)
    repo.write("README.md", "# projekt\n\nnowy akapit\n")
    repo.commit("tylko dokumentacja")
    change = _change(repo, base="main")
    events: list[ProgressEvent] = []
    control = RunControl(observer=events.append)

    run_gates(change, Policy(), gates=[CichaBramka(), DrugaBramka()], control=control)

    assert events[-1].completed == events[-1].total == 2


def test_awaria_odbiornika_nie_wywraca_przebiegu(repo):
    def wybuchowy(event: ProgressEvent) -> None:
        raise RuntimeError("baza padła")

    result = run_gates(
        _change(repo), Policy(), gates=[CichaBramka()], control=RunControl(observer=wybuchowy)
    )
    # Wynik jest ważniejszy niż pasek postępu.
    assert result.gate_results[0].status == "pass"


# ------------------------------------------------------------ anulowanie


def test_anulowanie_konczy_przebieg_bez_decyzji(repo):
    control = RunControl(cancelled=lambda: True, min_cancel_interval_s=0.0)

    with pytest.raises(RunCancelled):
        run_gates(_change(repo), Policy(), gates=[CichaBramka()], control=control)


def test_brak_control_zachowuje_stare_zachowanie(repo):
    result = run_gates(_change(repo), Policy(), gates=[CichaBramka()])
    assert result.decision is not None
    assert [g.gate for g in result.gate_results] == ["T.cicha"]


def _change(repo, base: str = "main"):
    from gatekeeper_core.core.change import ChangeContext

    if repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main":
        repo.checkout("praca", create=True)
        repo.write("src/app.py", "def hello():\n    return 'zmienione'\n")
        repo.commit("zmiana")
    return ChangeContext.from_git(repo.path, base)
