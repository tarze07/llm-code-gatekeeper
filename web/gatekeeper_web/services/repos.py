"""Rejestracja lokalnego repozytorium i wybór zakresu Git.

To jest druga granica zaufania panelu (po imporcie raportu). Operator podaje
tu ścieżkę i nazwy gałęzi, a panel na ich podstawie **uruchomi narzędzia na
kodzie**. Stąd (PLAN-WEB-UI.md §8):

* ścieżka musi po normalizacji leżeć w dozwolonym katalogu — sprawdzamy
  ścieżkę **rozwiniętą**, więc dowiązanie prowadzące poza katalog nie pomaga;
* to musi być korzeń repozytorium Git, nie dowolny katalog;
* nazwa gałęzi jest weryfikowana jako obiekt commit i przekazywana gitowi
  jako argument listy, po `--end-of-options`. Pole tekstowe nie ma prawa stać
  się opcją gita ani komendą powłoki;
* ta sama walidacja obowiązuje przy rejestracji **i** przy wykonaniu —
  katalog mógł się w międzyczasie zmienić.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gatekeeper_core.core.change import ChangeContext, GitError

#: Nazwa referencji Git. Świadomie węższa niż `git check-ref-format`: panel nie
#: musi obsługiwać egzotycznych nazw, a musi odrzucać `--upload-pack=…`.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$")

#: Ile referencji pokazujemy w formularzu. Repozytorium z 20 000 gałęzi nie ma
#: zawiesić strony.
MAX_REFS = 500

GIT_TIMEOUT_S = 20.0


class RepoError(ValueError):
    """Ścieżka albo referencja odrzucona — komunikat jest dla operatora."""


@dataclass(frozen=True)
class RepoRef:
    name: str
    kind: str
    sha: str
    subject: str = ""


@dataclass(frozen=True)
class ScopePreview:
    """Dokładne wejście kontroli, pokazane przed jej uruchomieniem."""

    base_ref: str
    head_ref: str
    base_tip_sha: str
    head_sha: str
    merge_base: str
    branch: str | None
    ticket: str | None
    total_files: int
    total_lines: int
    effective_files: int
    effective_lines: int
    generated_files: int
    test_files: int
    docs_only: bool
    paths: tuple[str, ...]

    @property
    def uses_merge_base(self) -> bool:
        """Diff liczymy od merge-base, nie od czubka gałęzi bazowej."""
        return self.merge_base != self.base_tip_sha


def resolve_repo_path(raw: str, allowed_roots: tuple[Path, ...]) -> Path:
    """Kanoniczna ścieżka repozytorium albo `RepoError` z powodem."""
    candidate = (raw or "").strip()
    if not candidate:
        raise RepoError("podaj ścieżkę do repozytorium")
    if "\x00" in candidate:
        raise RepoError("ścieżka zawiera znak zerowy")
    try:
        # `resolve()` rozwija `..` i dowiązania — dopiero wynik porównujemy
        # z dozwolonym katalogiem.
        path = Path(candidate).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise RepoError(f"ścieżka nie istnieje: {candidate}") from exc
    except (OSError, RuntimeError) as exc:
        raise RepoError(f"nie mogę odczytać ścieżki: {exc}") from exc

    if not path.is_dir():
        raise RepoError(f"to nie jest katalog: {path}")
    if not _within_allowed(path, allowed_roots):
        allowed = ", ".join(str(root) for root in allowed_roots) or "(brak)"
        raise RepoError(
            f"ścieżka {path} jest poza katalogami dozwolonymi dla panelu ({allowed}) "
            "— uruchom panel z `--repo-root`, jeśli ma widzieć ten katalog"
        )
    # `--show-toplevel` działa też z podkatalogu, więc jednym pytaniem
    # załatwiamy „czy to repo" i „czy to jego korzeń". Sama obecność `.git`
    # by nie wystarczyła: worktree ma tam plik, a podkatalog nie ma nic.
    try:
        root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    except RepoError as exc:
        raise RepoError(f"{path} nie jest repozytorium Git") from exc
    if root != path:
        raise RepoError(
            f"{path} jest podkatalogiem repozytorium — zarejestruj jego korzeń: {root}"
        )
    return path


def _within_allowed(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    for root in allowed_roots:
        try:
            resolved_root = root.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if path == resolved_root or resolved_root in path.parents:
            return True
    return False


def validate_ref_name(ref: str) -> str:
    ref = (ref or "").strip()
    if not _REF_RE.match(ref):
        raise RepoError(
            f"niedozwolona nazwa wersji Git: {ref!r} — dozwolone są litery, cyfry "
            "oraz `. _ / @ + -`"
        )
    return ref


def resolve_commit(repo: Path, ref: str) -> str:
    """Zamienia nazwę na SHA commita. Odrzuca wszystko, co commitem nie jest."""
    name = validate_ref_name(ref)
    try:
        # `--end-of-options` zamyka listę opcji: `--upload-pack=…` podane jako
        # nazwa gałęzi jest od tego miejsca argumentem, nie opcją gita.
        return _git(
            repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"
        )
    except RepoError as exc:
        raise RepoError(f"nie znam wersji {name!r} w tym repozytorium") from exc


def list_refs(repo: Path, limit: int = MAX_REFS) -> list[RepoRef]:
    """Gałęzie i tagi do wyboru w formularzu."""
    out = _git(
        repo,
        "for-each-ref",
        f"--count={limit}",
        "--sort=-committerdate",
        "--format=%(refname)%09%(refname:short)%09%(objectname)%09%(contents:subject)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    refs: list[RepoRef] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        full, short, sha = parts[0], parts[1], parts[2]
        subject = parts[3] if len(parts) > 3 else ""
        refs.append(RepoRef(name=short, kind=_kind(full), sha=sha, subject=subject[:120]))
    return refs


_KINDS = {"refs/heads/": "gałąź lokalna", "refs/remotes/": "gałąź zdalna", "refs/tags/": "tag"}


def _kind(full_refname: str) -> str:
    for prefix, label in _KINDS.items():
        if full_refname.startswith(prefix):
            return label
    return "referencja"


def preview_scope(
    repo: Path, base: str, head: str = "HEAD", ticket: str | None = None
) -> ScopePreview:
    """Dokładny zakres, który zobaczy silnik — liczony tym samym kodem co CLI."""
    base_name = validate_ref_name(base)
    head_name = validate_ref_name(head)
    base_tip = resolve_commit(repo, base_name)
    # Sprawdzamy też wersję ocenianą: ma być commitem, zanim silnik cokolwiek
    # z nią zrobi. SHA bierzemy potem z `ChangeContext`, żeby był ten sam.
    resolve_commit(repo, head_name)
    try:
        change = ChangeContext.from_git(repo, base_name, head_name, ticket_id=ticket)
    except GitError as exc:
        raise RepoError(f"nie umiem policzyć zakresu zmiany: {exc}") from exc

    return ScopePreview(
        base_ref=base_name,
        head_ref=head_name,
        base_tip_sha=base_tip,
        head_sha=change.head_sha,
        merge_base=change.base_sha,
        branch=change.branch,
        ticket=change.ticket.id if change.ticket else None,
        total_files=len(change.files),
        total_lines=change.total_lines,
        effective_files=len(change.effective_files),
        effective_lines=change.effective_lines,
        generated_files=len(change.files) - len(change.effective_files),
        test_files=sum(1 for f in change.files if f.test),
        docs_only=change.is_docs_only,
        paths=tuple(f.path for f in change.effective_files[:200]),
    )


def _git(repo: Path, *args: str) -> str:
    """Wywołanie gita listą argumentów. Nigdy przez powłokę."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - brak gita w systemie
        raise RepoError("nie znalazłem polecenia `git`") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoError("git nie odpowiedział w wyznaczonym czasie") from exc
    if proc.returncode != 0:
        raise RepoError((proc.stderr or proc.stdout).strip()[:300] or "git zwrócił błąd")
    return proc.stdout.strip()
