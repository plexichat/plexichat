"""
Repository for user notes.
"""

from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID


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
