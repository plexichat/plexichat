"""
Participant repository - Data access for conversation participants.
"""

from typing import Any, Dict, List, Optional

from ..models import Participant, ParticipantRole
from .base import BaseRepository
from src.core.base import SnowflakeID


class ParticipantRepository(BaseRepository[Participant]):
    """Repository for participant data access."""

    def create(
        self,
        part_id: SnowflakeID,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        joined_at: int,
        auto_commit: bool = True,
    ) -> None:
        """Create a new participant."""
        self._execute(
            """INSERT INTO msg_participants 
               (id, conversation_id, user_id, role, joined_at)
               VALUES (?, ?, ?, ?, ?)""",
            (part_id, conversation_id, user_id, role.value, joined_at),
            auto_commit=auto_commit,
        )

    def get_by_conversation_and_user(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get participant by conversation and user ID."""
        return self._fetch_one(
            "SELECT * FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )

    def get_all_by_conversation(
        self, conversation_id: SnowflakeID
    ) -> List[Dict[str, Any]]:
        """Get all participants in a conversation."""
        return self._fetch_all(
            "SELECT * FROM msg_participants WHERE conversation_id = ? ORDER BY joined_at",
            (conversation_id,),
        )

    def get_user_ids_by_conversation(
        self, conversation_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Get all participant user IDs in a conversation."""
        rows = self._fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,),
        )
        return [row["user_id"] for row in rows]

    def exists(self, conversation_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Check if user is a participant."""
        row = self._fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return row is not None

    def delete(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Remove a participant."""
        self._execute(
            "DELETE FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
            auto_commit=auto_commit,
        )

    def update_role(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        auto_commit: bool = True,
    ) -> None:
        """Update participant role."""
        self._execute(
            "UPDATE msg_participants SET role = ? WHERE conversation_id = ? AND user_id = ?",
            (role.value, conversation_id, user_id),
            auto_commit=auto_commit,
        )

    def update_mute(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        muted: bool,
        muted_until: Optional[int] = None,
        auto_commit: bool = True,
    ) -> None:
        """Update participant mute status."""
        self._execute(
            "UPDATE msg_participants SET muted = ?, muted_until = ? WHERE conversation_id = ? AND user_id = ?",
            (1 if muted else 0, muted_until, conversation_id, user_id),
            auto_commit=auto_commit,
        )

    def update_last_read(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        last_read_message_id: SnowflakeID,
        last_read_at: int,
        auto_commit: bool = True,
    ) -> None:
        """Update participant's last read position."""
        self._execute(
            """UPDATE msg_participants 
               SET last_read_message_id = ?, last_read_at = ? 
               WHERE conversation_id = ? AND user_id = ?""",
            (last_read_message_id, last_read_at, conversation_id, user_id),
            auto_commit=auto_commit,
        )

    def find_next_owner(
        self, conversation_id: SnowflakeID, exclude_user_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Find next suitable owner when current owner leaves."""
        row = self._fetch_one(
            """SELECT user_id FROM msg_participants 
               WHERE conversation_id = ? AND user_id != ? 
               ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined_at
               LIMIT 1""",
            (conversation_id, exclude_user_id),
        )
        return row["user_id"] if row else None

    def get_conversation_metadata(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get conversation metadata for server membership check."""
        row = self._fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?",
            (conversation_id,),
        )
        if row and row["metadata"]:
            return self._json_loads(row["metadata"])
        return None

    def check_server_membership(
        self, server_id: int, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a member of a server."""
        row = self._fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        return row is not None

    def row_to_model(self, row: Dict[str, Any]) -> Participant:
        """Convert database row to Participant model."""
        return Participant(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            role=ParticipantRole(row["role"]),
            joined_at=row["joined_at"],
            last_read_message_id=row["last_read_message_id"],
            last_read_at=row["last_read_at"],
            muted=bool(row["muted"]),
            muted_until=row["muted_until"],
            permissions=self._json_loads(row["permissions"]) if row["permissions"] else None,
            nickname=row["nickname"],
        )
