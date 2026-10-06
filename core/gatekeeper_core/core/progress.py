"""Obserwowanie przebiegu i anulowanie go w trakcie.

Silnik nie wie, kto go uruchomił: CLI wypisuje wynik na końcu, panel WWW musi
pokazywać postęp i umieć zatrzymać analizę. Zamiast dokładać do orkiestratora
wiedzę o obu tych światach, dostaje jeden opcjonalny obiekt sterujący.

Dwie rzeczy są tu świadome:

* **odbiornik zdarzeń działa w procesie nadzorującym**, nigdy w procesie
  bramki — inaczej niezaufany kod testów pisałby po bazie panelu;
* **postęp to „ukończono N z M kontroli"**, a nie procent pozostałego czasu.
  Bramki mają skrajnie różne budżety, więc procent czasu byłby zmyśleniem.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

EventKind = Literal[
    "plan",
    "wave_started",
    "gate_started",
    "gate_finished",
    "cancelling",
    "cancelled",
]


class RunCancelled(RuntimeError):
    """Przebieg zatrzymany na żądanie. Nie jest awarią i nie daje decyzji."""


@dataclass(frozen=True)
class ProgressEvent:
    kind: EventKind
    message: str = ""
    gate: str | None = None
    status: str | None = None
    completed: int = 0
    total: int = 0


@dataclass
class RunControl:
    """Odbiornik zdarzeń + pytanie „czy anulowano?".

    `cancelled` jest wołane w pętli oczekiwania, więc musi być tanie.
    Odpytywanie bazy przy każdym wywołaniu zostawiamy wywołującemu — od tego
    jest `min_cancel_interval_s`.
    """

    observer: Callable[[ProgressEvent], None] | None = None
    cancelled: Callable[[], bool] | None = None
    #: Górny limit oczekiwania w pętli bramek. Bez niego żądanie anulowania
    #: czekałoby na najbliższy budżet czasowy, czyli nawet kilka minut.
    poll_interval_s: float = 1.0
    min_cancel_interval_s: float = 0.5
    total: int = 0
    completed: int = 0
    _last_check: float = field(default=0.0, repr=False)
    _cancelled_cache: bool = field(default=False, repr=False)

    def emit(
        self,
        kind: EventKind,
        message: str = "",
        gate: str | None = None,
        status: str | None = None,
    ) -> None:
        if self.observer is None:
            return
        event = ProgressEvent(
            kind=kind,
            message=message,
            gate=gate,
            status=status,
            completed=self.completed,
            total=self.total,
        )
        # Awaria zapisu postępu nie ma prawa unieważnić analizy, która właśnie
        # się liczy. Wynik jest ważniejszy niż pasek postępu.
        with suppress(Exception):
            self.observer(event)

    def is_cancelled(self) -> bool:
        if self.cancelled is None:
            return False
        now = time.monotonic()
        if now - self._last_check >= self.min_cancel_interval_s:
            self._last_check = now
            # Niedostępna baza nie może udawać „anulowano" ani zawiesić pętli.
            with suppress(Exception):
                self._cancelled_cache = bool(self.cancelled())
        return self._cancelled_cache

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RunCancelled("przebieg anulowany na żądanie operatora")
