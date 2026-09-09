"""Kolejka, nadzorca i proces pojedynczego przebiegu.

Analizy **nie** biegną w procesie serwera HTTP. Silnik używa `fork` i zakłada
proces nadzorujący bez wątków, a serwer ASGI jest dokładnie odwrotnością tego
założenia (PLAN-WEB-UI.md §4). Stąd trzy osobne role:

* `spec` — zamrożone wejście przebiegu;
* `supervisor` — jeden proces, który bierze zadania z kolejki i pilnuje ich;
* `worker` — osobny program Pythona na jeden przebieg.
"""

from __future__ import annotations

from .spec import JobInputError, build_job_input, describe_input

__all__ = ["JobInputError", "build_job_input", "describe_input"]
