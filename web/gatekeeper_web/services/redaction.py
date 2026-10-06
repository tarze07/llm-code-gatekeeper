"""Redakcja danych z raportu przed zapisem i wyświetleniem.

Raport jest wejściem **niezaufanym**: powstaje z narzędzi uruchomionych na
cudzym kodzie, a przy imporcie pochodzi wprost z pliku wskazanego przez
operatora. Dwie rzeczy nie mają prawa trafić do panelu w oryginale
(PLAN-WEB-UI.md §8):

* ścieżki katalogów roboczych bramek (`/tmp/gatekeeper-wt-…`) — ujawniają
  układ hosta i kuszą, żeby zrobić z nich link do pobrania;
* wartości pól, których nazwa mówi „to jest sekret”.

Redakcja działa na wartościach, nie na kluczach: struktura raportu zostaje
taka, jaka była, więc adapter do modelu widoku nadal widzi oryginalne pola.
"""

from __future__ import annotations

import re
from typing import Any

#: Katalogi tymczasowe: to z nich bramki analizują kopię commita.
_TEMP_PATH_RE = re.compile(r"(?<![\w.])/(?:tmp|var/tmp|var/folders|run|dev/shm)/[\w./\-+@]*")

#: Nazwa pola deklarująca sekret. `tool_fingerprint` gitleaksa nie wpada tu
#: z nazwy, tylko przez redakcję ścieżki — i tak ma być.
_SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|api[_-]?key|private[_-]?key|authorization|credential)",
    re.IGNORECASE,
)

REDACTED = "[zredagowano]"

#: Pojedyncza wartość tekstowa w dowodzie. Pełny stdout narzędzia nie jest
#: dowodem, tylko zrzutem — i tak nikt go nie czyta w przeglądarce.
MAX_VALUE_CHARS = 2000
MAX_DEPTH = 6


def redact_text(value: str, limit: int = MAX_VALUE_CHARS) -> str:
    cleaned = _TEMP_PATH_RE.sub(_shorten_path, value)
    if len(cleaned) > limit:
        return cleaned[:limit] + f"… (obcięto {len(cleaned) - limit} znaków)"
    return cleaned


def redact_value(value: Any, key: str = "", depth: int = 0) -> Any:
    # Pola takie jak secrets.count i secrets.found są pomiarami. Maskujemy
    # treść sekretu, w tym cały kontener, zanim zgubimy nazwę jego rodzica.
    if key and _SECRET_KEY_RE.search(key) and isinstance(value, (str, dict, list)):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return REDACTED
        return {k: redact_value(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if depth >= MAX_DEPTH:
            return REDACTED
        return [redact_value(v, key, depth + 1) for v in value]
    return value


def _shorten_path(match: re.Match[str]) -> str:
    path = match.group(0)
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return f"…/{tail}" if tail else "…"
