"""Blokada pliku na jednego właściciela — POSIX i Windows.

POSIX: `fcntl.flock` na całym pliku. Windows nie ma `fcntl`; `msvcrt.locking`
blokuje zakres bajtów i działa tylko w trybie bez czekania, czyli dokładnie
tak, jak tego potrzebujemy: zajęta blokada = „ktoś już pracuje", a nie
zawieszenie procesu.

Na Windows blokujemy jeden bajt daleko za treścią pliku. Blokada zakresu
w Windows jest obowiązkowa (inne uchwyty nie przeczytają ani nie zapiszą
zablokowanych bajtów), więc bajt 0 zablokowałby też odczyt zapisanej
w pliku informacji o właścicielu. Blokowanie poza końcem pliku jest dozwolone.

Obie blokady znikają razem z procesem — także zabitym — więc martwy właściciel
nie blokuje następcy.
"""

from __future__ import annotations

import errno
import os
import sys
from contextlib import suppress
from pathlib import Path

#: Gdzie na Windows leży blokowany bajt. Musi być taki sam we wszystkich
#: procesach, które sprawdzają tę samą blokadę.
_WINDOWS_LOCK_OFFSET = 1 << 30

#: Błędy oznaczające „zajęte przez kogoś innego", a nie awarię.
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLK", -1)}


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _try_lock(fd: int) -> bool:
    """`True` = blokada nasza; `False` = trzyma ją ktoś inny; inne błędy lecą dalej."""
    try:
        _lock(fd)
    except OSError as exc:
        if exc.errno in _BUSY:
            return False
        raise
    return True


class FileLock:
    """Wyłączna blokada pliku na czas życia obiektu (albo procesu)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self, owner: str = "") -> bool:
        """Bierze blokadę bez czekania. `False`, gdy trzyma ją ktoś inny.

        Plik otwieramy bez obcinania: na Windows obcięcie pliku z cudzą
        blokadą zakresu kończy się błędem, zanim w ogóle spróbujemy blokady.
        """
        if self._fd is not None:
            return True
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not _try_lock(fd):
                os.close(fd)
                return False
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        if owner:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{owner}\n".encode())
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        # Zamknięcie i tak zwalnia blokadę — błąd odblokowania niczego nie zmienia.
        with suppress(OSError):
            _unlock(fd)
        os.close(fd)


def is_locked(path: Path) -> bool:
    """Czy ktoś *inny* trzyma blokadę `path`. Nie tworzy pliku."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        if not _try_lock(fd):
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)
