"""Eksport przebiegu: JSON, Markdown i samodzielny HTML.

Markdown renderuje **core**, a nie panel — dzięki temu raport pobrany
z panelu jest tym samym tekstem, który trafia do komentarza w PR. Jedyna
różnica to limit znalezisk: w komentarzu jest celowy, w archiwum byłby
przekłamaniem (PLAN-WEB-UI.md §3).

Eksport HTML jest samodzielnym plikiem: bez JavaScriptu, bez CDN-a, ze stylem
w treści. Raport ma się otwierać z dysku za rok, a nie wykonywać cokolwiek.
"""

from __future__ import annotations

import json
from typing import Any

from gatekeeper_core.core.finding import RunResult
from gatekeeper_core.core.report import render_markdown

from ..templating import environment
from .view import RunView

_env = environment()


def report_json(payload: dict[str, Any]) -> str:
    """Zapisany raport w oryginalnym kształcie (po redakcji z importu)."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def report_markdown(run: RunResult) -> str:
    # Bez limitu: panel pokazuje wszystkie znaleziska, więc eksport też.
    return render_markdown(run, max_findings=max(len(run.findings), 1))


def report_html(view: RunView) -> str:
    return _env.get_template("export.html").render(run=view)
