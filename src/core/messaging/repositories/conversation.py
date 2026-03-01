"""
Conversation repository - Data access for conversations.
"""

from typing import Any, Dict, List, Optional

from ..models import Conversation, ConversationType
from .base import BaseRepository
from src.core.base import SnowflakeID


class ConversationRepository(BaseRepository[Conversation]):
    """Repository for conversation data access."""

    def create(
        self,
        conv_id: SnowflakeID,
        conversation_type: ConversationType,
        created_at: int,
        name: Optional[str] = None,
        owner_id: Optional[SnowflakeID] = None,
        max_participants: int = 100,
        encrypted: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        auto_commit: bool = True,
    ) -> None:
        """Create a new conversation."""
        self._execute(
            """INSERT INTO msg_conversations 
               (id, conversation_type, name, owner_id, max_participants, 
                created_at, updated_at, encrypted, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conv_id,
                conversation_type.value,
                name,
                owner_id,
                max_participants,
                created_at,
                created_at,
                1 if encrypted else 0,
                self._json_dumps(metadata),
            ),
            auto_commit=auto_commit,
        )

    def get_by_id(self, conv_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get conversation by ID with participant count."""
        return self._fetch_one(
            """SELECT c.*, 
                      (SELECT COUNT(*) FROM msg_participants WHERE conversation_id = c.id) as participant_count
               FROM msg_conversations c
               WHERE c.id = ? AND c.deleted = 0""",
            (conv_id,),
        )

    def get_user_conversations(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        conversation_type: Optional[ConversationType] = None,
    ) -> List[Dict[str, Any]]:
        """Get conversations for a user with pagination."""
        query = """
            SELECT c.*, counts.participant_count
            FROM msg_conversations c
            INNER JOIN msg_participants p ON c.id = p.conversation_id
            LEFT JOIN (
                SELECT conversation_id, COUNT(*) as participant_count 
                FROM msg_participants 
                GROUP BY conversation_id
            ) counts ON c.id = counts.conversation_id
            WHERE p.user_id = ? AND c.deleted = 0
        """
        params: List[Any] = [user_id]

        if conversation_type:
            query += " AND c.conversation_type = ?"
            params.append(conversation_type.value)

        if before_id:
            query += " AND c.id < ?"
            params.append(before_id)

        query += " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?"
        params.append(limit)

        return self._fetch_all(query, tuple(params))

    def update(
        self,
        conv_id: SnowflakeID,
        updated_at: int,
        name: Optional[str] = None,
        max_participants: Optional[int] = None,
        last_message_id: Optional[SnowflakeID] = None,
        last_message_at: Optional[int] = None,
        auto_commit: bool = True,
    ) -> None:
        """Update conversation fields."""
        updates = ["updated_at = ?"]
        params: List[Any] = [updated_at]

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if max_participants is not None:
            updates.append("max_participants = ?")
            params.append(max_participants)
        if last_message_id is not None:
            updates.append("last_message_id = ?")
            params.append(last_message_id)
        if last_message_at is not None:
            updates.append("last_message_at = ?")
            params.append(last_message_at)

        params.append(conv_id)
        self._execute(
            f"UPDATE msg_conversations SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
            auto_commit=auto_commit,
        )

    def soft_delete(
        self, conv_id: SnowflakeID, deleted_at: int, auto_commit: bool = True
    ) -> None:
        """Soft delete a conversation."""
        self._execute(
            "UPDATE msg_conversations SET deleted = 1, deleted_at = ? WHERE id = ?",
            (deleted_at, conv_id),
            auto_commit=auto_commit,
        )

    def update_owner(
        self, conv_id: SnowflakeID, new_owner_id: SnowflakeID, auto_commit: bool = True
    ) -> None:
        """Update conversation owner."""
        self._execute(
            "UPDATE msg_conversations SET owner_id = ? WHERE id = ?",
            (new_owner_id, conv_id),
            auto_commit=auto_commit,
        )

    def get_dm_lookup(
        self, user1_id: SnowflakeID, user2_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Get existing DM conversation ID between two users."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        row = self._fetch_one(
            "SELECT conversation_id FROM msg_dm_lookup WHERE user1_id = ? AND user2_id = ?",
            (u1, u2),
        )
        return row["conversation_id"] if row else None

    def create_dm_lookup(
        self,
        lookup_id: SnowflakeID,
        user1_id: SnowflakeID,
        user2_id: SnowflakeID,
        conv_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Create DM lookup entry."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        self._execute(
            "INSERT INTO msg_dm_lookup (id, user1_id, user2_id, conversation_id) VALUES (?, ?, ?, ?)",
            (lookup_id, u1, u2, conv_id),
            auto_commit=auto_commit,
        )

    def delete_dm_lookup(self, conv_id: SnowflakeID, auto_commit: bool = True) -> None:
        """Delete DM lookup entry."""
        self._execute(
            "DELETE FROM msg_dm_lookup WHERE conversation_id = ?",
            (conv_id,),
            auto_commit=auto_commit,
        )

    def get_notes_conversation(self, user_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get user's notes conversation."""
        return self._fetch_one(
            """SELECT c.id FROM msg_conversations c
               INNER JOIN msg_participants p ON c.id = p.conversation_id
               WHERE c.conversation_type = ? AND p.user_id = ? AND c.deleted = 0""",
            (ConversationType.NOTES.value, user_id),
        )

    def row_to_model(self, row: Dict[str, Any]) -> Conversation:
        """Convert database row to Conversation model."""
        participant_count = 0
        try:
            participant_count = row["participant_count"]
        except (KeyError, IndexError):
            pass

        return Conversation(
            id=row["id"],
            conversation_type=ConversationType(row["conversation_type"]),
            name=row["name"],
            owner_id=row["owner_id"],
            max_participants=row["max_participants"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_message_id=row["last_message_id"],
            last_message_at=row["last_message_at"],
            encrypted=bool(row["encrypted"]),
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
            participant_count=participant_count,
            metadata=self._json_loads(row["metadata"]) if row["metadata"] else None,
        )



