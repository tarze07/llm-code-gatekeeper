"""Środowisko Jinja2 wspólne dla stron panelu i eksportu HTML.

`autoescape` jest włączony dla wszystkich rozszerzeń, nie tylko `.html`:
tytuły znalezisk, ścieżki i dowody pochodzą z cudzego repozytorium i
z narzędzi zewnętrznych, więc każdy z nich jest potencjalnym `<script>`
(PLAN-WEB-UI.md §8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=1)
def environment() -> Environment:
    """Jedno środowisko na proces — szablony kompilują się raz."""
    return build_environment()


def build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=True, default_for_string=True),
        # Literówka w nazwie zmiennej ma wywalić szablon, a nie wypisać pustkę
        # w miejscu, gdzie miała być decyzja polityki.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["czas"] = _fmt_datetime
    env.filters["sekundy"] = _fmt_seconds
    env.filters["sha"] = _fmt_sha
    return env


def _fmt_datetime(value: datetime | str | None) -> str:
    if value is None:
        return "brak danych"
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            # Czas z cudzego raportu bywa dowolnym tekstem — pokazujemy go
            # takim, jaki jest, zamiast udawać, że go rozumiemy.
            return value
        value = parsed
    if value.tzinfo:
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return value.strftime("%Y-%m-%d %H:%M")


def _fmt_seconds(value: Any) -> str:
    if value is None:
        return "brak danych"
    seconds = float(value)
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    return f"{int(seconds // 60)} min {int(seconds % 60)} s"


def _fmt_sha(value: str | None) -> str:
    return (value or "")[:12] or "—"
