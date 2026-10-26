"""Polskie znaki na konsoli Windows.

Konsola i przekierowany strumień na Windows dostają domyślnie stronę kodową
systemu (np. cp1252), w której nie ma „ł" ani „ś" — `print` kończy się wtedy
`UnicodeEncodeError` i proces pada na komunikacie, a nie na błędzie.
Przełączamy strumienie na UTF-8 z zastępowaniem znaków: komunikat może
wyglądać krzywo w starej konsoli, ale nigdy nie wywraca procesu.
"""

from __future__ import annotations

import io
import os
import sys


def utf8_console() -> None:
    # `os.name`, nie `sys.platform`: mypy na Linuksie uznałby resztę za martwą.
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
