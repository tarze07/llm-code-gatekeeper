"""Zamrożone wejście przebiegu.

Zadanie wykonuje dokładnie to, co zapisano w chwili zatwierdzenia formularza —
nawet jeśli w międzyczasie przesunie się gałąź albo operator aktywuje inną
politykę (PLAN-WEB-UI.md §5). Dlatego zamrażamy **SHA**, a nie nazwy gałęzi,
i **treść** polityki, a nie wskaźnik na profil.

Czego nie obiecujemy: identycznego wyniku SCA w czasie. Rejestry podatności
się zmieniają i powtórzenie tego samego zakresu jutro może zwrócić inne
znaleziska — to cecha danych, nie błąd zamrażania.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .. import __version__ as web_version
from ..services.environment import PACKAGES, package_version
from ..services.repos import ScopePreview
from ..storage import PolicyRevision, Project, revision_payload

#: Wejście zadania jest wersjonowane osobno od raportu: worker musi umieć
#: odmówić wykonania wejścia, którego nie rozumie.
INPUT_VERSION = 1

#: Pliki blokad zależności. Ich skróty trafiają do wejścia, żeby dało się
#: powiedzieć, czy „ten sam zakres" miał te same zależności.
LOCKFILES = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "packages.lock.json",
    "Cargo.lock",
    "go.sum",
)


class JobInputError(ValueError):
    pass


def build_job_input(
    project: Project,
    revision: PolicyRevision,
    preview: ScopePreview,
    *,
    gates: tuple[str, ...] | None = None,
    fast_path: bool = True,
    ticket: str | None = None,
) -> dict[str, Any]:
    if not project.repo_path:
        raise JobInputError(
            f"projekt {project.name!r} nie ma zarejestrowanej ścieżki repozytorium"
        )
    repo = Path(project.repo_path)
    return {
        "input_version": INPUT_VERSION,
        "project": {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "repo_path": str(repo),
            # Tożsamość repozytorium niezależna od ścieżki: ta sama praca
            # przeniesiona do innego katalogu nadal jest tym samym repo.
            "repo_identity": repo_identity(repo),
        },
        "scope": {
            "base_ref": preview.base_ref,
            "head_ref": preview.head_ref,
            "base_tip_sha": preview.base_tip_sha,
            "head_sha": preview.head_sha,
            # Diff liczy się od merge-base — i to on jest zamrażany, bo to on
            # decyduje o zawartości zmiany.
            "merge_base": preview.merge_base,
            "branch": preview.branch,
            "effective_files": preview.effective_files,
            "effective_lines": preview.effective_lines,
        },
        "ticket": ticket or preview.ticket,
        "gates": list(gates) if gates else None,
        "fast_path": fast_path,
        "policy": revision_payload(revision),
        "versions": versions(),
        "lockfiles": lockfile_digests(repo),
    }


def repo_identity(repo: Path) -> str:
    """Skrót pierwszego commita. Pusty, gdy nie da się go ustalić."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return ""
    roots = proc.stdout.split()
    return roots[-1] if roots else ""


def versions() -> dict[str, Any]:
    return {
        "web": web_version,
        "packages": {name: package_version(name) for name in PACKAGES},
    }


def lockfile_digests(repo: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for name in LOCKFILES:
        path = repo / name
        try:
            if path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
                digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:  # pragma: no cover
            continue
    return digests


def describe_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Skrót wejścia do pokazania operatorowi — bez treści polityki."""
    policy = payload.get("policy") or {}
    scope = payload.get("scope") or {}
    return {
        "base_ref": scope.get("base_ref"),
        "head_ref": scope.get("head_ref"),
        "merge_base": scope.get("merge_base"),
        "head_sha": scope.get("head_sha"),
        "branch": scope.get("branch"),
        "effective_files": scope.get("effective_files"),
        "effective_lines": scope.get("effective_lines"),
        "ticket": payload.get("ticket"),
        "gates": payload.get("gates"),
        "fast_path": payload.get("fast_path"),
        "policy_revision": policy.get("revision"),
        "policy_hash": (policy.get("content_hash") or "")[:16],
        "lockfiles": payload.get("lockfiles") or {},
        "versions": payload.get("versions") or {},
    }


def require_supported(payload: dict[str, Any]) -> None:
    version = payload.get("input_version")
    if version != INPUT_VERSION:
        raise JobInputError(
            f"wejście zadania w wersji {version!r}, a ten worker zna {INPUT_VERSION} "
            "— zadanie pochodzi z nowszego panelu"
        )
