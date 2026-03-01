"""
Pin repository - Data access for pinned messages.
"""

from typing import Any, Dict, List, Optional

from ..models import PinnedMessage
from .base import BaseRepository
from src.core.base import SnowflakeID


class PinRepository(BaseRepository[PinnedMessage]):
    """Repository for pinned message data access."""

    def create(
        self,
        pin_id: SnowflakeID,
        conversation_id: SnowflakeID,
        message_id: SnowflakeID,
        pinned_by: SnowflakeID,
        pinned_at: int,
        auto_commit: bool = True,
    ) -> None:
        """Create a new pin entry."""
        self._execute(
            """INSERT INTO msg_pinned (id, conversation_id, message_id, pinned_by, pinned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (pin_id, conversation_id, message_id, pinned_by, pinned_at),
            auto_commit=auto_commit,
        )

    def get_by_message(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get pin info for a message."""
        return self._fetch_one(
            "SELECT * FROM msg_pinned WHERE message_id = ?",
            (message_id,),
        )

    def get_batch_by_messages(
        self, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Get pin info for multiple messages (batch operation)."""
        if not message_ids:
            return {}

        in_clause, params = self._build_in_clause(message_ids)
        rows = self._fetch_all(
            f"SELECT * FROM msg_pinned WHERE message_id IN {in_clause}",  # nosec B608
            params,
        )

        return {row["message_id"]: row for row in rows}

    def exists(self, message_id: SnowflakeID) -> bool:
        """Check if message is pinned."""
        row = self._fetch_one(
            "SELECT 1 FROM msg_pinned WHERE message_id = ?",
            (message_id,),
        )
        return row is not None

    def count_by_conversation(self, conversation_id: SnowflakeID) -> int:
        """Count pinned messages in a conversation."""
        row = self._fetch_one(
            "SELECT COUNT(*) as count FROM msg_pinned WHERE conversation_id = ?",
            (conversation_id,),
        )
        return row["count"] if row else 0

    def delete(self, message_id: SnowflakeID, auto_commit: bool = True) -> None:
        """Delete a pin entry."""
        self._execute(
            "DELETE FROM msg_pinned WHERE message_id = ?",
            (message_id,),
            auto_commit=auto_commit,
        )

    def get_pinned_messages(self, conversation_id: SnowflakeID) -> List[Dict[str, Any]]:
        """Get all pinned messages in a conversation with message data."""
        return self._fetch_all(
            """SELECT m.*, p.pinned_by, p.pinned_at FROM msg_messages m
               INNER JOIN msg_pinned p ON m.id = p.message_id
               WHERE p.conversation_id = ? AND m.deleted = 0
               ORDER BY p.pinned_at DESC""",
            (conversation_id,),
        )

    def row_to_model(self, row: Dict[str, Any]) -> PinnedMessage:
        """Convert database row to PinnedMessage model."""
        return PinnedMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            pinned_by=row["pinned_by"],
            pinned_at=row["pinned_at"],
        )

