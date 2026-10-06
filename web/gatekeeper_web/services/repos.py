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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gatekeeper_core.core.change import ChangeContext, GitError

#: Nazwa referencji Git. Świadomie węższa niż `git check-ref-format`: panel nie
#: musi obsługiwać egzotycznych nazw, a musi odrzucać `--upload-pack=…`.
#:
#: `^` i `~` są dozwolone, bo `60d1046^` i `master~2` to najkrótszy sposób
#: powiedzenia „rodzic tego commita" — a bez nich trzeba było wyszukiwać SHA
#: rodzica ręcznie. Bezpieczeństwa to nie rusza: pierwszy znak nadal musi być
#: alfanumeryczny (więc nazwa nie stanie się opcją), a całość i tak jedzie do
#: gita po `--end-of-options` i musi rozwiązać się do obiektu commit.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+~^-]{0,199}$")

#: Ile referencji pokazujemy w formularzu. Repozytorium z 20 000 gałęzi nie ma
#: zawiesić strony.
MAX_REFS = 500

#: Ile commitów wypisujemy w podglądzie zakresu. Gałąź odbiegająca o tysiąc
#: commitów to sygnał sam w sobie — pokazujemy początek listy i mówimy wprost,
#: że jest ucięta, zamiast renderować wszystko albo milczeć.
MAX_COMMITS = 200

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
class RepoCommit:
    """Jeden commit z ocenianego zakresu."""

    sha: str
    author: str
    date: str
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:12]


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
    #: Commity w ocenianym zakresie, od najnowszego. Domyślnie puste, żeby
    #: istniejące wywołania konstruktora nie musiały nic wiedzieć o historii.
    commits: tuple[RepoCommit, ...] = ()
    #: Ile commitów jest naprawdę — `len(commits)` bywa ucięte do `MAX_COMMITS`.
    total_commits: int = 0

    @property
    def commits_truncated(self) -> bool:
        return self.total_commits > len(self.commits)

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
            "oraz `. _ / @ + - ~ ^`"
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
        raise RepoError(
            f"nie znam wersji {name!r} w tym repozytorium{_available_refs(repo)}"
        ) from exc


def _available_refs(repo: Path, limit: int = 8) -> str:
    """Końcówka komunikatu: czego operator może użyć zamiast tego, co wpisał.

    Sama informacja „nie znam tej wersji" zostawia go ze zgadywanką — zwłaszcza
    gdy repozytorium ma `master`, a panel podpowiedział `main`.
    """
    try:
        refs = list_refs(repo, limit=limit)
    except RepoError:  # pragma: no cover - błąd gita już zgłosiliśmy wyżej
        return ""
    if not refs:
        return ""
    return " — dostępne m.in.: " + ", ".join(ref.name for ref in refs)


def ref_exists(repo: Path, ref: str) -> bool:
    try:
        resolve_commit(repo, ref)
    except RepoError:
        return False
    return True


#: Typowe nazwy gałęzi integracyjnej, sprawdzane po kolei, gdy repozytorium nie
#: deklaruje własnej przez `origin/HEAD`.
FALLBACK_BASE_REFS = ("main", "master", "develop", "trunk")


def default_base_ref(repo: Path) -> str:
    """Gałąź bazowa podpowiadana w formularzu — pusto, gdy nie ma pewnej.

    Wpisane na sztywno `main` było zgadywanką udającą wiedzę: w repozytorium
    z `master` formularz startował z wartością, która musiała dać błąd. Pytamy
    więc repozytorium, a gdy nie umie odpowiedzieć — zostawiamy pole puste
    i oddajemy wybór liście podpowiedzi. Puste pole jest uczciwsze niż nazwa,
    o której wiadomo tylko tyle, że bywa popularna.
    """
    try:
        declared = _git(repo, "symbolic-ref", "--short", "--quiet", "refs/remotes/origin/HEAD")
    except RepoError:
        declared = ""
    if declared:
        # `origin/main` → wolimy lokalne `main`, jeśli istnieje: diff liczy się
        # tak samo, a nazwa jest ta, którą operator zna.
        for candidate in (declared.removeprefix("origin/"), declared):
            if candidate and ref_exists(repo, candidate):
                return candidate

    for candidate in FALLBACK_BASE_REFS:
        if ref_exists(repo, candidate):
            return candidate
    return ""


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


#: Ile ostatnich commitów trafia na listę wyboru wersji.
MAX_PICKABLE_COMMITS = 50


def recent_commits(repo: Path, limit: int = MAX_PICKABLE_COMMITS) -> tuple[RepoCommit, ...]:
    """Ostatnie commity z **całego** repozytorium, do wyboru jako wersja.

    `--all`, a nie tylko bieżąca gałąź: commit, od którego chce się liczyć diff,
    bardzo często nie jest czubkiem niczego — to zwykle rodzic ocenianej zmiany,
    na który nie wskazuje żadna gałąź ani tag. Bez tej listy trzeba było
    wyszukać jego SHA poza panelem i przepisać ręcznie.
    """
    out = _git(
        repo,
        "log",
        "--all",
        f"--max-count={limit}",
        "--format=%H%x09%an%x09%aI%x09%s",
    )
    commits: list[RepoCommit] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        commits.append(
            RepoCommit(sha=parts[0], author=parts[1][:120], date=parts[2], subject=parts[3][:200])
        )
    return tuple(commits)


def list_commits(
    repo: Path, base_sha: str, head_sha: str, limit: int = MAX_COMMITS
) -> tuple[tuple[RepoCommit, ...], int]:
    """Commity z zakresu `base_sha..head_sha` plus ich pełna liczba.

    Zakres jest ten sam, z którego liczy się diff — czyli od **merge-base**,
    nie od czubka gałęzi bazowej. Lista, która pokazywałaby coś innego niż
    oceniany diff, wprowadzałaby w błąd dokładnie tam, gdzie operator decyduje.

    SHA podaje git (`rev-parse`), nie formularz, więc do zakresu nie trafia
    tekst od użytkownika. `--end-of-options` zostaje mimo to — dokładnie jak
    przy `resolve_commit`.
    """
    if base_sha == head_sha:
        return (), 0

    zakres = f"{base_sha}..{head_sha}"
    total = _git(repo, "rev-list", "--count", "--end-of-options", zakres)
    out = _git(
        repo,
        "log",
        f"--max-count={limit}",
        "--format=%H%x09%an%x09%aI%x09%s",
        "--end-of-options",
        zakres,
    )
    commits: list[RepoCommit] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        commits.append(
            RepoCommit(
                sha=parts[0],
                author=parts[1][:120],
                date=parts[2],
                # Temat commita jedzie prosto z cudzego repozytorium — ucinamy
                # go tu, a szablon i tak escapuje.
                subject=parts[3][:200],
            )
        )
    try:
        liczba = int(total)
    except ValueError:  # pragma: no cover - `rev-list --count` zwraca liczbę
        liczba = len(commits)
    return tuple(commits), liczba


_KINDS = {"refs/heads/": "gałąź lokalna", "refs/remotes/": "gałąź zdalna", "refs/tags/": "tag"}

#: Kolejność grup na liście wyboru. Gałąź lokalna jest tym, czego operator
#: szuka najczęściej, tag — najrzadziej.
_KIND_ORDER = ("gałąź lokalna", "gałąź zdalna", "tag", "referencja")


def group_refs(refs: Sequence[RepoRef]) -> list[tuple[str, list[RepoRef]]]:
    """Referencje pogrupowane do listy wyboru, w stałej kolejności rodzajów.

    Wewnątrz grupy zostaje kolejność z `list_refs` (od ostatnio commitowanej),
    bo to ona odpowiada na pytanie „nad czym ostatnio pracowałem".
    """
    grouped: dict[str, list[RepoRef]] = {}
    for ref in refs:
        grouped.setdefault(ref.kind, []).append(ref)
    return [(kind, grouped[kind]) for kind in _KIND_ORDER if kind in grouped]


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

    commits, total_commits = list_commits(repo, change.base_sha, change.head_sha)

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
        commits=commits,
        total_commits=total_commits,
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
