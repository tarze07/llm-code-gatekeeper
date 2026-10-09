"""Operacje na systemie plików, które muszą zachowywać się tak samo na Linuksie i Windows.

* `is_link` — dowiązanie symboliczne **albo** junction NTFS. Junction nie jest
  symlinkiem w rozumieniu `Path.is_symlink()`, a przekierowuje ścieżkę tak samo,
  więc każda kontrola izolacji musi traktować oba przypadki jednakowo.
* `link_dir` — dowiązanie katalogu, które działa na Windows bez uprawnień.
* `remove_tree` — sprzątanie kopii roboczej. Git na Windows zapisuje obiekty
  jako tylko do odczytu, przez co `shutil.rmtree` zawodzi; ciche
  `ignore_errors=True` zostawiałoby kopie kodu PR-a w katalogu tymczasowym bez
  żadnego śladu.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import warnings
from collections.abc import Callable
from pathlib import Path


def is_link(path: Path) -> bool:
    """Prawda dla dowiązania symbolicznego i dla junction (Windows)."""
    return path.is_symlink() or path.is_junction()


def link_dir(link: Path, target: Path) -> None:
    """Dowiązanie do katalogu: junction na Windows (bez Developer Mode
    i uprawnień administratora), symlink gdzie indziej."""
    if sys.platform == "win32":
        import _winapi  # type: ignore[import-not-found,unused-ignore]

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _make_writable(path: str) -> None:
    mode = os.lstat(path).st_mode
    extra = stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if stat.S_ISDIR(mode) else 0)
    os.chmod(path, stat.S_IMODE(mode) | extra)


_RETRYABLE: tuple[Callable[..., object], ...] = (os.unlink, os.rmdir, os.remove)


def _clear_readonly_and_retry(
    function: Callable[..., object], path: str, exc: BaseException
) -> None:
    # Windows blokuje usunięcie pliku z atrybutem tylko do odczytu, POSIX —
    # wpisu w katalogu bez prawa zapisu. Zdejmujemy oba i ponawiamy raz;
    # kolejny błąd przechodzi do `remove_tree`.
    if not isinstance(exc, PermissionError) or function not in _RETRYABLE:
        raise exc
    parent = os.path.dirname(path)
    if parent:
        _make_writable(parent)
    if os.path.lexists(path):
        _make_writable(path)
    function(path)


def remove_tree(path: Path | str) -> bool:
    """Usuwa drzewo katalogów, zdejmując atrybut tylko do odczytu w razie potrzeby.

    Zwraca `True`, gdy katalogu już nie ma. Gdy usunięcie się nie powiedzie,
    emituje `RuntimeWarning` zamiast przemilczeć pozostawioną kopię; wyjątku nie
    podnosi, bo sprzątanie działa w `finally` i nie może zasłonić właściwego błędu.
    """
    target = Path(path)
    if not target.exists() and not is_link(target):
        return True
    try:
        if is_link(target):
            # Nie podążamy za dowiązaniem — usuwamy wyłącznie je samo.
            if target.is_symlink():
                target.unlink()
            else:
                os.rmdir(target)
        else:
            shutil.rmtree(target, onexc=_clear_readonly_and_retry)
    except OSError as exc:
        warnings.warn(
            f"nie udało się usunąć katalogu tymczasowego {target}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return False
    return True
