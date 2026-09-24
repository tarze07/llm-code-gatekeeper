"""Wspólna usługa uruchamiania kontroli.

Wcześniej cała ta logika — wczytaj politykę, zwaliduj ją, wybierz bramki,
policz zakres zmiany, uruchom — mieszkała w `cli.run()`. Panel WWW musiałby
ją wtedy powtórzyć, a dwie kopie walidacji polityki rozjeżdżają się przy
pierwszej poprawce (PLAN-WEB-UI.md §5).

Tutaj zostaje samo *przygotowanie i wykonanie*. Formatowanie komunikatów,
kody wyjścia i odpowiedzi HTTP należą do adapterów: `cli.py` i pakietu
`gatekeeper_web`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..gates import Gate, build_gates, known_facts, known_gate_ids
from .change import ChangeContext, GitError
from .finding import RunResult
from .orchestrator import run_gates
from .policy import Policy, PolicyError
from .progress import RunControl

#: Bramka, której trzeba jawnie wskazać mapę zakresu. Panel trzyma politykę
#: operatora poza ocenianym repozytorium, więc domyślna ścieżka względna
#: (`policy/scope_map.yaml` w kopii ocenianego commita) wskazywałaby plik
#: pochodzący od ocenianego, a nie od operatora.
SCOPE_GATE = "G0.scope"


class PreparationError(RuntimeError):
    """Nie da się nawet zacząć: zła polityka, zły zakres Git, nieznana bramka.

    Odróżniona od awarii w trakcie przebiegu, bo znaczy co innego: nic się
    jeszcze nie wykonało i nie ma czego raportować.
    """


@dataclass(frozen=True)
class RunRequest:
    """Kompletne wejście kontroli. Wszystko jawne, nic ze zmiennych środowiska."""

    repo: Path
    base: str
    head: str = "HEAD"
    policy_path: Path = Path("policy/gates.yaml")
    exceptions_path: Path | None = None
    #: Bezwzględna ścieżka do mapy zakresu operatora. `None` = zachowanie CLI,
    #: czyli `policy/scope_map.yaml` z ocenianego repozytorium.
    scope_map_path: Path | None = None
    ticket: str | None = None
    gates: tuple[str, ...] | None = None
    fast_path: bool = True
    max_workers: int = 4


@dataclass
class PreparedRun:
    """Zwalidowane wejście gotowe do wykonania — bez żadnego procesu w tle."""

    request: RunRequest
    policy: Policy
    change: ChangeContext
    gates: list[Gate] = field(default_factory=list)

    @property
    def gate_ids(self) -> list[str]:
        return [g.id for g in self.gates]


def load_policy(request: RunRequest) -> Policy:
    """Wczytuje i **lintuje** politykę. Literówka w fakcie to błąd, nie ostrzeżenie."""
    try:
        policy = Policy.load(request.policy_path, request.exceptions_path)
    except (OSError, PolicyError) as exc:
        raise PreparationError(f"polityka: {exc}") from exc

    errors = policy.lint(known_facts(), known_gate_ids())
    if errors:
        raise PreparationError("polityka: " + "; ".join(errors))

    if request.scope_map_path is not None:
        # Snapshot mapy zakresu musi *faktycznie* trafić do bramki, a nie
        # tylko leżeć w bazie panelu (PLAN-WEB-UI.md §5).
        gates = dict(policy.gates)
        config = dict(gates.get(SCOPE_GATE) or {})
        config["scope_map_path"] = str(Path(request.scope_map_path).resolve())
        gates[SCOPE_GATE] = config
        policy.gates = gates
    return policy


def prepare(request: RunRequest) -> PreparedRun:
    """Waliduje politykę, wybór bramek i zakres Git. Nie uruchamia niczego."""
    policy = load_policy(request)
    try:
        gates = build_gates(policy, only=request.gates)
    except ValueError as exc:
        raise PreparationError(str(exc)) from exc
    try:
        change = ChangeContext.from_git(
            request.repo, request.base, request.head, ticket_id=request.ticket
        )
    except GitError as exc:
        raise PreparationError(f"zakres zmiany: {exc}") from exc
    return PreparedRun(request=request, policy=policy, change=change, gates=gates)


def execute(prepared: PreparedRun, control: RunControl | None = None) -> RunResult:
    """Uruchamia przygotowaną kontrolę. `control` przenosi postęp i anulowanie."""
    return run_gates(
        prepared.change,
        prepared.policy,
        gates=prepared.gates,
        fast_path=prepared.request.fast_path,
        max_workers=prepared.request.max_workers,
        control=control,
    )


def run_check(request: RunRequest, control: RunControl | None = None) -> RunResult:
    return execute(prepare(request), control=control)
