"""Konfiguracja panelu: katalog stanu i granice zaufania HTTP."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Stan panelu żyje **poza** ocenianymi repozytoriami (PLAN-WEB-UI.md §5).
#: `.gatekeeper/runs.db` w ocenianym repo pozostaje bazą CLI i panel jej nie dotyka.
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "gatekeeper-web"

#: Nasłuch wyłącznie na pętli zwrotnej. Zmiana na `0.0.0.0` nie jest
#: „udostępnieniem zespołowi” — to wystawienie wykonywania narzędzi na kodzie
#: bez uwierzytelnienia (PLAN-WEB-UI.md §8 i §10).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

#: Nagłówek `Host` spoza tej listy jest odrzucany zanim dotrze do widoku —
#: inaczej DNS rebinding zamienia panel lokalny w panel publiczny.
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")

#: Katalogi, w których wolno zarejestrować repozytorium. Panel uruchamia na
#: nim narzędzia, więc „dowolna ścieżka z formularza" byłaby zaproszeniem do
#: analizowania `/etc` albo cudzego katalogu domowego (PLAN-WEB-UI.md §8).
#: Domyślnie katalog domowy operatora; zawężane przez `--repo-root`.
DEFAULT_REPO_ROOTS: tuple[Path, ...] = (Path.home(),)

#: Jedno zadanie naraz. Równoległość *wewnątrz* przebiegu zostaje bez zmian —
#: chodzi o to, żeby nie przemnożyć zużycia pamięci i miejsca na kopie repo.
MAX_CONCURRENT_JOBS = 1


@dataclass(frozen=True)
class Settings:
    state_dir: Path = DEFAULT_STATE_DIR
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = field(default=DEFAULT_ALLOWED_HOSTS)
    allowed_repo_roots: tuple[Path, ...] = field(default=DEFAULT_REPO_ROOTS)
    #: Nadzorca zadań startuje razem z panelem. Wyłączany w testach API,
    #: które nie mają nic uruchamiać.
    start_supervisor: bool = True
    #: Sesja operatora (PLAN-WEB-UI.md §8). Panel lokalny startuje **bez**
    #: logowania: nasłuch i tak jest tylko na pętli zwrotnej, a jednorazowy kod
    #: w terminalu okazał się kosztem bez odbiorcy dla jednego operatora.
    #: `serve --wymagaj-logowania` włącza sesję z kodem startowym z powrotem.
    require_login: bool = False
    session_secret: str = ""
    bootstrap_code: str = ""
    bootstrap_hash: str = ""

    @property
    def db_path(self) -> Path:
        return self.state_dir / "panel.db"

    @property
    def supervisor_lock_path(self) -> Path:
        return self.state_dir / "supervisor.lock"

    @property
    def work_dir(self) -> Path:
        """Katalogi robocze zadań: snapshoty polityki, kopie repozytorium."""
        return self.state_dir / "prace"

    def ensure_state_dir(self) -> Path:
        # 0700: baza zawiera treść raportów z cudzych repozytoriów.
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.state_dir

    @classmethod
    def from_env(cls) -> Settings:
        raw = os.environ.get("GATEKEEPER_WEB_STATE_DIR")
        roots = os.environ.get("GATEKEEPER_WEB_REPO_ROOTS")
        return cls(
            state_dir=Path(raw).expanduser() if raw else DEFAULT_STATE_DIR,
            host=os.environ.get("GATEKEEPER_WEB_HOST", DEFAULT_HOST),
            port=int(os.environ.get("GATEKEEPER_WEB_PORT", str(DEFAULT_PORT))),
            allowed_repo_roots=(
                tuple(Path(part) for part in roots.split(os.pathsep) if part)
                if roots
                else DEFAULT_REPO_ROOTS
            ),
            # `--reload` startuje nowy proces, więc tryb logowania musi
            # przejechać przez środowisko razem z sekretem i skrótem kodu.
            require_login=os.environ.get("GATEKEEPER_WEB_REQUIRE_LOGIN", "") == "1",
            session_secret=os.environ.get("GATEKEEPER_WEB_SESSION_SECRET", ""),
            bootstrap_hash=os.environ.get("GATEKEEPER_WEB_BOOTSTRAP_HASH", ""),
        )
