"""
Repository for user notes.
"""

from typing import Optional, List, Dict, Any, Iterator
from src.core.base import SnowflakeID


# A page of users can carry hundreds of notes to hydrate. Bind the target ids
# in bounded slices instead of building one enormous ``IN (...)`` statement:
# 500 matches the bulk chunk size used by the other back-end repositories and
# stays well below SQLite's bind-variable cap on every supported build. The
# author's ``user_id`` is bound alongside every chunk, so a batched read can
# never observe another user's notes.
MAX_NOTES_READ_CHUNK = 500


def _chunk_target_ids(
    target_ids: List[SnowflakeID], size: int = MAX_NOTES_READ_CHUNK
) -> Iterator[List[SnowflakeID]]:
    """Yield bounded slices of target ids for ``IN``-clause binding."""
    for start in range(0, len(target_ids), size):
        yield target_ids[start : start + size]


class UserNotesRepository:
    """Repository for user notes operations."""

    def __init__(self, db: Any) -> None:
        """Initialize repository with database instance."""
        self._db = db

    def create(
        self,
        note_id: SnowflakeID,
        user_id: SnowflakeID,
        target_user_id: SnowflakeID,
        note_encrypted: str,
        created_at: int,
        updated_at: int,
    ) -> None:
        """Create or replace a user note atomically.

        ``auth_user_notes`` enforces ``UNIQUE(user_id, target_user_id)``. Callers
        previously raced an ``exists()``/``get()`` check followed by a plain
        INSERT, so two concurrent writes for the same note both saw "missing"
        and one raised ``IntegrityError`` (losing the note). Folding the write
        into a single ``INSERT ... ON CONFLICT DO UPDATE`` enforces the
        uniqueness constraint at the database boundary: the statement is
        atomic, concurrent callers can no longer interleave, and the latest
        content wins without duplicating rows.

        The conflict update deliberately leaves ``id`` and ``created_at``
        untouched so a note keeps its stable identity and creation history.
        """
        self._db.execute(
            """INSERT INTO auth_user_notes
               (id, user_id, target_user_id, note_encrypted, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, target_user_id) DO UPDATE SET
                   note_encrypted = EXCLUDED.note_encrypted,
                   updated_at = EXCLUDED.updated_at""",
            (note_id, user_id, target_user_id, note_encrypted, created_at, updated_at),
        )

    def update(
        self,
        note_id: SnowflakeID,
        note_encrypted: str,
        updated_at: int,
    ) -> None:
        """Update an existing user note."""
        self._db.execute(
            """UPDATE auth_user_notes
               SET note_encrypted = ?, updated_at = ?
               WHERE id = ?""",
            (note_encrypted, updated_at, note_id),
        )

    def get(
        self, user_id: SnowflakeID, target_user_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get a user's note about another user."""
        return self._db.fetch_one(
            """SELECT id, user_id, target_user_id, note_encrypted, created_at, updated_at
               FROM auth_user_notes
               WHERE user_id = ? AND target_user_id = ?""",
            (user_id, target_user_id),
        )

    def get_all(self, user_id: SnowflakeID) -> List[Dict[str, Any]]:
        """Get all notes by a user."""
        return self._db.fetch_all(
            """SELECT id, user_id, target_user_id, note_encrypted, created_at, updated_at
               FROM auth_user_notes
               WHERE user_id = ?
               ORDER BY updated_at DESC, id DESC""",
            (user_id,),
        )

    def get_many(
        self,
        user_id: SnowflakeID,
        target_user_ids: List[SnowflakeID],
        *,
        chunk_size: int = MAX_NOTES_READ_CHUNK,
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Load one author's notes about many targets in bounded queries.

        This is the batched replacement for calling :meth:`get` once per
        target (an N+1 read). Target ids are de-duplicated and bound in chunks
        of at most ``chunk_size``. The ``user_id`` predicate is present in
        every chunk, so batching preserves per-user scoping: a caller can only
        ever retrieve notes authored by ``user_id``.

        Returns a mapping keyed by ``target_user_id``. Since
        ``auth_user_notes`` enforces ``UNIQUE(user_id, target_user_id)``, at
        most one row exists per target. Targets with no note are absent from
        the mapping.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        # Preserve input order while removing duplicates so a repeated target
        # never occupies extra bind slots.
        unique_targets = list(dict.fromkeys(target_user_ids))
        if not unique_targets:
            return {}

        notes: Dict[SnowflakeID, Dict[str, Any]] = {}
        for chunk in _chunk_target_ids(unique_targets, chunk_size):
            placeholders = ", ".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                f"""SELECT id, user_id, target_user_id, note_encrypted, created_at, updated_at
                   FROM auth_user_notes
                   WHERE user_id = ? AND target_user_id IN ({placeholders})
                   ORDER BY updated_at DESC, id DESC""",
                (user_id, *chunk),
            )
            for row in rows:
                target_id = row["target_user_id"] if isinstance(row, dict) else row[2]
                notes[target_id] = row
        return notes

    def delete(self, user_id: SnowflakeID, target_user_id: SnowflakeID) -> bool:
        """Delete a user note."""
        self._db.execute(
            "DELETE FROM auth_user_notes WHERE user_id = ? AND target_user_id = ?",
            (user_id, target_user_id),
        )
        return True

    def exists(self, user_id: SnowflakeID, target_user_id: SnowflakeID) -> bool:
        """Check if a note exists."""
        row = self._db.fetch_one(
            "SELECT 1 FROM auth_user_notes WHERE user_id = ? AND target_user_id = ?",
            (user_id, target_user_id),
        )
        return row is not None
