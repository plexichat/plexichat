"""
Repository for message edit history.
"""

from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID

# A single history read must never be able to pull an unbounded number of
# encrypted blobs into memory. When more versions exist, the newest
# ``MAX_EDIT_HISTORY_ROWS`` are returned (in ascending version order).
MAX_EDIT_HISTORY_ROWS = 200


def _bounded_history_limit(limit: Optional[int]) -> int:
    """Clamp a caller-supplied history limit into a safe, positive range."""
    if limit is None:
        return MAX_EDIT_HISTORY_ROWS
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return MAX_EDIT_HISTORY_ROWS
    return max(1, min(value, MAX_EDIT_HISTORY_ROWS))


class EditHistoryRepository:
    """Repository for message edit history operations."""

    def __init__(self, db: Any) -> None:
        """Initialize repository with database instance."""
        self._db = db

    def create(
        self,
        edit_id: SnowflakeID,
        message_id: SnowflakeID,
        editor_id: SnowflakeID,
        old_content: str,
        old_content_encrypted: Optional[str],
        edit_timestamp: int,
        version_number: int,
    ) -> None:
        """Create a new edit history entry."""
        self._db.execute(
            """INSERT INTO msg_edit_history
               (id, message_id, editor_id, old_content, old_content_encrypted, edit_timestamp, version_number)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                edit_id,
                message_id,
                editor_id,
                old_content,
                old_content_encrypted,
                edit_timestamp,
                version_number,
            ),
        )

    def get_by_message(
        self, message_id: SnowflakeID, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get bounded edit history for a message, ordered by version number.

        At most ``MAX_EDIT_HISTORY_ROWS`` rows are fetched regardless of the
        requested ``limit``. The query selects the newest versions first and
        the rows are reversed so callers still observe ascending order.
        """
        bounded = _bounded_history_limit(limit)
        rows = self._db.fetch_all(
            """SELECT id, message_id, editor_id, old_content, old_content_encrypted,
                      edit_timestamp, version_number
               FROM msg_edit_history
               WHERE message_id = ?
               ORDER BY version_number DESC
               LIMIT ?""",
            (message_id, bounded),
        )
        rows.reverse()
        return rows

    def get_latest_version(self, message_id: SnowflakeID) -> Optional[int]:
        """Get the latest version number for a message."""
        row = self._db.fetch_one(
            """SELECT MAX(version_number) as max_version
               FROM msg_edit_history
               WHERE message_id = ?""",
            (message_id,),
        )
        return row["max_version"] if row else None

    def delete_by_message(self, message_id: SnowflakeID) -> None:
        """Delete all edit history for a message (cascades on message delete)."""
        self._db.execute(
            "DELETE FROM msg_edit_history WHERE message_id = ?",
            (message_id,),
        )

    def count_edits(self, message_id: SnowflakeID) -> int:
        """Count total edits for a message."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_edit_history WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0
