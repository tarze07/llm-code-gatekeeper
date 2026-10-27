"""Rejestr bramek.

Kontrakt jest jeden i celowo wąski: `Gate.run(ChangeContext) -> GateResult`.
Nowa bramka nie wymaga dotykania rdzenia (TOOLS.md §9) — dosłownie: bramki
rejestrują się przez entry points (grupa `gatekeeper.gates`), nie przez
wpisanie do listy importów tutaj. Ten pakiet (`gatekeeper`) rejestruje przez
ten sam mechanizm swoje własne jedenaście bramek (`pyproject.toml`) — rdzeń
je swój własny dogfood, więc mechanizm jest udowodniony niezależnie od tego,
czy jakikolwiek zewnętrzny pakiet z bramkami jest w ogóle zainstalowany.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any

from ..core.change import ChangeContext
from ..core.finding import GateResult
from ..core.runner import is_forbidden_env_var

GATE_GROUP = "gatekeeper.gates"


class Gate:
    id: str = ""
    name: str = ""
    budget_s: float = 60.0
    #: Fakty, które ta bramka może wystawić. Służą do walidacji polityki —
    #: literówka w `gates.yaml` ma być błędem, nie regułą, która nigdy nie
    #: zadziała.
    facts: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def run(self, change: ChangeContext) -> GateResult:  # pragma: no cover - interfejs
        raise NotImplementedError

    @classmethod
    def declared_facts(cls) -> tuple[str, ...]:
        """Domyślnie `cls.facts`. Bramki-agregatory (`G1.static`, `G1.deps`,
        `G2.*`, `G3.sca`, `G3.sast`) nadpisują to, żeby dorzucić fakty ze
        WSZYSTKICH zainstalowanych dostawców poziomu 2 (patrz `core/plugins.py`)
        — inaczej `gatekeeper policy facts`/`policy lint` nie znałyby faktu
        pluginu, którego akurat nie ma w danym środowisku."""
        return cls.facts

    @classmethod
    def config_errors(cls, config: dict[str, Any]) -> list[str]:
        """Błędy w sekcji `gates.<id>` polityki, wykrywalne bez uruchamiania.

        `policy lint` woła to dla każdej skonfigurowanej bramki, żeby literówka
        w konfiguracji wyszła przy walidacji, a nie dopiero przy starcie
        przebiegu. Domyślnie bramka niczego nie sprawdza."""
        return []

    @classmethod
    def config_warnings(cls, config: dict[str, Any]) -> list[str]:
        """Ostrzeżenia w sekcji `gates.<id>` polityki, np. niezalecany `keep_env`."""
        return []

    # pomocnicze
    def result(self, **kwargs: Any) -> GateResult:
        return GateResult(gate=self.id, **kwargs)


REGISTRY: dict[str, type[Gate]] = {}


def _same_class(a: type[Gate], b: type[Gate]) -> bool:
    # Porównanie po nazwie, nie tylko po tożsamości: przeładowany moduł
    # (np. `importlib.reload` w testach) daje nowy obiekt tej samej klasy.
    return a is b or (a.__module__, a.__qualname__) == (b.__module__, b.__qualname__)


def register(cls: type[Gate]) -> type[Gate]:
    """Rejestruje bramkę pod jej `id`.

    Ta sama klasa może przyjść dwa razy (dekorator i entry point) — to nie
    błąd. Inna klasa pod zajętym `id` już tak: pack, który omyłkowo zgłosi
    np. `G1.static`, byłby inaczej po cichu zignorowany albo po cichu
    podmieniłby bramkę core (REVIEW.md §5, P2)."""
    if not cls.id:
        raise ValueError(f"{cls.__name__} nie ma identyfikatora")
    existing = REGISTRY.get(cls.id)
    if existing is not None and not _same_class(existing, cls):
        raise ValueError(
            f"bramka {cls.id!r} jest już zarejestrowana przez "
            f"{existing.__module__}.{existing.__qualname__}; "
            f"{cls.__module__}.{cls.__qualname__} nie może jej zastąpić"
        )
    REGISTRY[cls.id] = cls
    return cls


def all_gates() -> list[type[Gate]]:
    for ep in entry_points(group=GATE_GROUP):
        register(ep.load())
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def known_facts() -> set[str]:
    return {fact for gate in all_gates() for fact in gate.declared_facts()}


def known_gate_ids() -> set[str]:
    return {gate.id for gate in all_gates()}


def gate_config_errors(policy: Any) -> list[str]:
    """`Gate.config_errors()` dla każdej bramki skonfigurowanej w `policy.gates`.

    Nieznane identyfikatory pomija — te zgłasza już `Policy.lint()`."""
    by_id = {cls.id: cls for cls in all_gates()}
    errors: list[str] = []
    for gate_id in sorted(policy.gates):
        cls = by_id.get(gate_id)
        if cls is None:
            continue
        config = policy.gate_config(gate_id)
        keep_env = config.get("keep_env")
        if keep_env:
            if isinstance(keep_env, (list, tuple)):
                forbidden = [str(var) for var in keep_env if is_forbidden_env_var(str(var))]
                if forbidden:
                    msg = (
                        f"`gates.{gate_id}`: `keep_env` zawiera zakazane nazwy poświadczeń: "
                        f"{', '.join(forbidden)} — polityka ocenianego repozytorium nie może "
                        "przekazywać tokenów ani haseł"
                    )
                    errors.append(msg)
            else:
                errors.append(f"`gates.{gate_id}`: `keep_env` musi być listą nazw zmiennych")
        errors.extend(
            f"`gates.{gate_id}`: {e}" for e in cls.config_errors(config)
        )
    return errors


def gate_config_warnings(policy: Any) -> list[str]:
    """`Gate.config_warnings()` dla każdej bramki skonfigurowanej w `policy.gates`."""
    by_id = {cls.id: cls for cls in all_gates()}
    warnings: list[str] = []
    for gate_id in sorted(policy.gates):
        cls = by_id.get(gate_id)
        if cls is None:
            continue
        config = policy.gate_config(gate_id)
        if "keep_env" in config:
            warnings.append(
                f"`gates.{gate_id}`: `keep_env` w polityce ocenianego repozytorium "
                "jest niezalecane — konfiguracja zmiennych środowiskowych przysługuje "
                "operatorowi poprzez zmienną GATEKEEPER_KEEP_ENV"
            )
        warnings.extend(
            f"`gates.{gate_id}`: {w}" for w in cls.config_warnings(config)
        )
    return warnings


def build_gates(policy: Any, only: Iterable[str] | None = None) -> list[Gate]:
    selected = set(only) if only else None
    available = all_gates()
    unknown = (selected or set()) - {cls.id for cls in available}
    if unknown:
        raise ValueError(f"nieznane bramki: {', '.join(sorted(unknown))}")
    gates: list[Gate] = []
    for cls in available:
        if selected and cls.id not in selected:
            continue
        gates.append(cls(policy.gate_config(cls.id)))
    return gates
