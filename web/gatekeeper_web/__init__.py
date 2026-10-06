"""Panel WWW dla llm-code-gatekeeper.

Pakiet opcjonalny: `core` (i CLI `gatekeeper`) działa bez niego, a panel
zależy od `core` tylko w jedną stronę — nie odwrotnie.

Zakres tego wydania to etapy 0–6 z `PLAN-WEB-UI.md`: przeglądarka raportów,
rejestr projektów, kolejka z nadzorcą, oceny, metryki, kod startowy operatora
i kopia zapasowa bazy. Granica jest w `CONTRACT.md`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
