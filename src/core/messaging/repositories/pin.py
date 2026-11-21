"""
Pin repository - Data access for pinned messages.
"""

from typing import Any, Dict, List, Optional

from ..models import PinnedMessage
from .base import BaseRepository
from src.core.base import SnowflakeID


# The pins listing route caps its response at 200 messages. Bound the storage
# read to the same ceiling so a conversation with a very large pin set cannot
# make the cold path fetch (and hydrate into Message models) every pin only for
# the route to discard all but one page. SQLite treats LIMIT -1 as *unlimited*,
# so clamp explicitly rather than trusting the caller.
MAX_PIN_FETCH = 200


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
            "SELECT * FROM msg_pinned WHERE message_id IN {ids}".format(ids=in_clause),
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

    def get_pinned_messages(
        self,
        conversation_id: SnowflakeID,
        limit: int = MAX_PIN_FETCH,
    ) -> List[Dict[str, Any]]:
        """Get the most recently pinned messages in a conversation (bounded).

        Ordered by ``pinned_at DESC`` so the cap keeps exactly the rows the
        listing route would return; the route's own slice stays as a defensive
        no-op. The participant gate still lives in the service, so bounding the
        read does not change who may see the rows.
        """
        limit = max(1, min(int(limit), MAX_PIN_FETCH))
        return self._fetch_all(
            """SELECT m.*, p.pinned_by, p.pinned_at FROM msg_messages m
               INNER JOIN msg_pinned p ON m.id = p.message_id
               WHERE p.conversation_id = ? AND m.deleted = 0
               ORDER BY p.pinned_at DESC LIMIT ?""",
            (conversation_id, limit),
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
