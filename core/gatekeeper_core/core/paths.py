"""Normalizacja ścieżek niezależna od systemu, na którym biegnie proces.

Narzędzia zwracają ścieżki w postaci posixowej, windowsowej (`C:\\x`, `C:/x`),
jako `file://` albo względem katalogu roboczego kontenera (`/work/...`).
Porównanie ze zmienionymi plikami działa tylko na jednej postaci — a każda
ścieżka, która nie pasuje, po cichu wypada z filtrowania do diffa (fail-open).
Dlatego cała logika siedzi tu, na czystych stringach (`PurePosixPath` /
`PureWindowsPath`), żeby dało się ją testować na Linuksie.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse

#: Gdzie sandbox kontenerowy montuje katalog roboczy repozytorium.
CONTAINER_WORKDIR = "/work"

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_URI_DRIVE_RE = re.compile(r"^/[A-Za-z]:[\\/]")


def is_windows_absolute(path: str) -> bool:
    """`C:\\x`, `C:/x` albo UNC (`\\\\host\\share`) — niezależnie od systemu."""
    return bool(_DRIVE_RE.match(path)) or path.startswith("\\\\")


def is_absolute_path(path: str) -> bool:
    """Bezwzględna w sensie posixowym albo windowsowym."""
    return path.startswith("/") or is_windows_absolute(path)


def _file_uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    raw = unquote(parsed.path)
    if _URI_DRIVE_RE.match(raw):  # file:///C:/x → C:/x
        return raw[1:]
    if is_windows_absolute(f"{parsed.netloc}/"):  # file://C:/x — dysk w miejscu hosta
        return f"{parsed.netloc}{raw}"
    if parsed.netloc and parsed.netloc != "localhost":  # file://host/share/x → UNC
        return f"//{parsed.netloc}{raw}"
    return raw


def _under_container_workdir(path: str) -> str | None:
    posix = PurePosixPath(path)
    root = PurePosixPath(CONTAINER_WORKDIR)
    if posix == root:
        return ""
    try:
        return posix.relative_to(root).as_posix()
    except ValueError:
        return None


def to_repo_relative(path: str, repo: Path) -> str:
    """Ścieżka repo-względna w stylu posix; spoza repo — bezwzględna posix."""
    if not path:
        return ""
    if path.startswith("file://"):
        path = _file_uri_to_path(path)
    if is_windows_absolute(path):
        win = PureWindowsPath(path)
        repo_s = str(repo)
        if is_windows_absolute(repo_s):
            try:
                return win.relative_to(PureWindowsPath(repo_s)).as_posix()
            except ValueError:
                pass
        return win.as_posix()
    if path.startswith("/"):
        candidate = Path(path)
        try:
            return candidate.resolve().relative_to(repo.resolve()).as_posix()
        except (ValueError, OSError):
            pass
        aliased = _under_container_workdir(path)
        if aliased is not None:
            return aliased
        return PurePosixPath(path).as_posix()
    return PureWindowsPath(path).as_posix().removeprefix("./")
