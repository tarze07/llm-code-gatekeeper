"""Profile polityki i ich wersje.

Polityka, według której zapadła decyzja, musi dać się odtworzyć razem
z przebiegiem. Dlatego wersja po aktywacji jest **niezmienna**, a zadanie
zamraża jej treść u siebie — zmiana profilu jutro nie przepisuje wczorajszego
raportu (PLAN-WEB-UI.md §6).

Panel trzyma politykę operatora **poza** ocenianym repozytorium. Gdyby brał ją
z ocenianego commita, oceniany agent mógłby zmienić kryteria własnej oceny.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .db import Database
from .repository import slugify


class PolicyStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyProfile:
    id: int
    slug: str
    name: str
    created_at: str
    active_revision_id: int | None


@dataclass(frozen=True)
class PolicyRevision:
    id: int
    profile_id: int
    revision: int
    state: str
    policy_yaml: str
    exceptions_yaml: str | None
    scope_map_yaml: str | None
    content_hash: str
    author: str | None
    note: str | None
    created_at: str
    activated_at: str | None

    @property
    def is_active(self) -> bool:
        return self.state == "active"

    @property
    def label(self) -> str:
        return f"v{self.revision}"


def content_hash(policy: str, exceptions: str | None, scope_map: str | None) -> str:
    payload = "\x1e".join([policy, exceptions or "", scope_map or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PolicyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------- profile

    def create_profile(self, name: str) -> PolicyProfile:
        base = slugify(name)
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            slug, suffix = base, 2
            while conn.execute("SELECT 1 FROM policy_profiles WHERE slug = ?", (slug,)).fetchone():
                slug = f"{base}-{suffix}"
                suffix += 1
            cursor = conn.execute(
                "INSERT INTO policy_profiles (slug, name, created_at) VALUES (?,?,?)",
                (slug, name.strip(), now),
            )
            profile_id = int(cursor.lastrowid or 0)
        return PolicyProfile(profile_id, slug, name.strip(), now, None)

    def list_profiles(self) -> list[PolicyProfile]:
        with self.db.reading() as conn:
            rows = conn.execute("SELECT * FROM policy_profiles ORDER BY name COLLATE NOCASE")
            return [_profile(row) for row in rows]

    def get_profile(self, profile_id: int) -> PolicyProfile | None:
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT * FROM policy_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return _profile(row) if row else None

    # ------------------------------------------------------------- wersje

    def create_revision(
        self,
        profile_id: int,
        policy_yaml: str,
        exceptions_yaml: str | None = None,
        scope_map_yaml: str | None = None,
        author: str | None = None,
        note: str | None = None,
    ) -> PolicyRevision:
        """Zawsze jako szkic. Aktywacja jest osobnym, jawnym działaniem."""
        now = datetime.now(UTC).isoformat()
        digest = content_hash(policy_yaml, exceptions_yaml, scope_map_yaml)
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM policy_profiles WHERE id = ?", (profile_id,)) \
                    .fetchone() is None:
                raise PolicyStoreError(f"nie znam profilu polityki o ID {profile_id}")
            row = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) AS n FROM policy_revisions WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            revision = int(row["n"]) + 1
            cursor = conn.execute(
                """INSERT INTO policy_revisions
                   (profile_id, revision, state, policy_yaml, exceptions_yaml, scope_map_yaml,
                    content_hash, author, note, created_at)
                   VALUES (?,?,'draft',?,?,?,?,?,?,?)""",
                (
                    profile_id, revision, policy_yaml, exceptions_yaml, scope_map_yaml,
                    digest, author, note, now,
                ),
            )
            revision_id = int(cursor.lastrowid or 0)
        created = self.get_revision(revision_id)
        assert created is not None
        return created

    def get_revision(self, revision_id: int) -> PolicyRevision | None:
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT * FROM policy_revisions WHERE id = ?", (revision_id,)
            ).fetchone()
        return _revision(row) if row else None

    def list_revisions(self, profile_id: int) -> list[PolicyRevision]:
        with self.db.reading() as conn:
            return [
                _revision(row)
                for row in conn.execute(
                    "SELECT * FROM policy_revisions WHERE profile_id = ? ORDER BY revision DESC",
                    (profile_id,),
                )
            ]

    def active_revision(self, profile_id: int) -> PolicyRevision | None:
        with self.db.reading() as conn:
            row = conn.execute(
                "SELECT r.* FROM policy_revisions r"
                " JOIN policy_profiles p ON p.active_revision_id = r.id"
                " WHERE p.id = ?",
                (profile_id,),
            ).fetchone()
        return _revision(row) if row else None

    def activate(
        self, revision_id: int, author: str | None = None, note: str | None = None
    ) -> PolicyRevision:
        """Aktywacja dotyczy **przyszłych** zadań. Nie rusza zapisanych raportów."""
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM policy_revisions WHERE id = ?", (revision_id,)
            ).fetchone()
            if row is None:
                raise PolicyStoreError(f"nie znam wersji polityki o ID {revision_id}")
            if row["state"] == "retired":
                raise PolicyStoreError(
                    "ta wersja została już wycofana — utwórz nowy szkic zamiast wskrzeszać starą"
                )
            profile_id = int(row["profile_id"])
            conn.execute(
                "UPDATE policy_revisions SET state = 'retired' WHERE profile_id = ?"
                " AND state = 'active' AND id != ?",
                (profile_id, revision_id),
            )
            conn.execute(
                "UPDATE policy_revisions SET state = 'active', activated_at = ?,"
                " author = COALESCE(?, author), note = COALESCE(?, note) WHERE id = ?",
                (now, author, note, revision_id),
            )
            conn.execute(
                "UPDATE policy_profiles SET active_revision_id = ? WHERE id = ?",
                (revision_id, profile_id),
            )
            conn.execute(
                "INSERT INTO audit_events (at, kind, project_id, run_id, detail)"
                " VALUES (?,?,?,?,?)",
                (now, "policy.activated", None, None,
                 f"profil={profile_id} wersja={row['revision']} autor={author or '—'}"),
            )
        activated = self.get_revision(revision_id)
        assert activated is not None
        return activated


def _profile(row: sqlite3.Row) -> PolicyProfile:
    return PolicyProfile(
        id=int(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        created_at=str(row["created_at"]),
        active_revision_id=row["active_revision_id"],
    )


def _revision(row: sqlite3.Row) -> PolicyRevision:
    return PolicyRevision(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        revision=int(row["revision"]),
        state=str(row["state"]),
        policy_yaml=str(row["policy_yaml"]),
        exceptions_yaml=row["exceptions_yaml"],
        scope_map_yaml=row["scope_map_yaml"],
        content_hash=str(row["content_hash"]),
        author=row["author"],
        note=row["note"],
        created_at=str(row["created_at"]),
        activated_at=row["activated_at"],
    )


def revision_payload(revision: PolicyRevision) -> dict[str, Any]:
    """Kształt zamrażany w wejściu zadania."""
    return {
        "profile_id": revision.profile_id,
        "revision_id": revision.id,
        "revision": revision.revision,
        "content_hash": revision.content_hash,
        "policy_yaml": revision.policy_yaml,
        "exceptions_yaml": revision.exceptions_yaml,
        "scope_map_yaml": revision.scope_map_yaml,
    }
