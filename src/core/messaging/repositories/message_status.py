"""
Message status repository - Data access for delivery/read status.
"""

from typing import Any, Dict, List, Optional

from ..models import MessageStatus, MessageStatusType
from .base import BaseRepository
from src.core.base import SnowflakeID


class MessageStatusRepository(BaseRepository[MessageStatus]):
    """Repository for message status data access."""

    def create(
        self,
        status_id: SnowflakeID,
        message_id: SnowflakeID,
        user_id: SnowflakeID,
        status: MessageStatusType,
        timestamp: int,
        auto_commit: bool = True,
    ) -> None:
        """Create a new message status entry."""
        self._execute(
            """INSERT INTO msg_message_status (id, message_id, user_id, status, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (status_id, message_id, user_id, status.value, timestamp),
            auto_commit=auto_commit,
        )

    def get_by_message_and_user(
        self, message_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get status for a specific message and user."""
        return self._fetch_one(
            "SELECT * FROM msg_message_status WHERE message_id = ? AND user_id = ?",
            (message_id, user_id),
        )

    def get_all_by_message(self, message_id: SnowflakeID) -> List[Dict[str, Any]]:
        """Get all status entries for a message."""
        return self._fetch_all(
            "SELECT * FROM msg_message_status WHERE message_id = ? ORDER BY timestamp",
            (message_id,),
        )

    def update_status(
        self,
        message_id: SnowflakeID,
        user_id: SnowflakeID,
        status: MessageStatusType,
        timestamp: int,
        auto_commit: bool = True,
    ) -> None:
        """Update existing status entry."""
        self._execute(
            "UPDATE msg_message_status SET status = ?, timestamp = ? WHERE message_id = ? AND user_id = ?",
            (status.value, timestamp, message_id, user_id),
            auto_commit=auto_commit,
        )

    def get_batch_for_user(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, MessageStatusType]:
        """Get status for multiple messages for a user (batch operation)."""
        if not message_ids:
            return {}

        in_clause, params = self._build_in_clause(message_ids)
        rows = self._fetch_all(
            f"SELECT message_id, status FROM msg_message_status WHERE user_id = ? AND message_id IN {in_clause}",
            (user_id,) + params,
        )
        return {row["message_id"]: MessageStatusType(row["status"]) for row in rows}

    def get_batch_counts(
        self, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Dict[str, int]]:
        """Get delivery and read counts for multiple messages (batch operation)."""
        if not message_ids:
            return {}

        in_clause, params = self._build_in_clause(message_ids)
        rows = self._fetch_all(
            f"""SELECT message_id, 
                       COUNT(CASE WHEN status IN ('delivered', 'read') THEN 1 END) as delivery_count,
                       COUNT(CASE WHEN status = 'read' THEN 1 END) as read_count
                FROM msg_message_status 
                WHERE message_id IN {in_clause}
                GROUP BY message_id""",
            params,
        )
        return {
            row["message_id"]: {
                "delivery_count": row["delivery_count"],
                "read_count": row["read_count"],
            }
            for row in rows
        }

    def batch_mark_delivered(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        timestamp: int,
        status_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> int:
        """Mark multiple messages as delivered in batch."""
        if not message_ids:
            return 0

        in_clause, params = self._build_in_clause(message_ids)

        # Update existing non-read statuses
        self._execute(
            f"""UPDATE msg_message_status 
                SET status = ?, timestamp = ?
                WHERE user_id = ? AND status NOT IN ('delivered', 'read')
                AND message_id IN {in_clause}""",
            (MessageStatusType.DELIVERED.value, timestamp, user_id) + params,
            auto_commit=auto_commit,
        )

        # Insert for messages without status (using INSERT OR IGNORE for SQLite)
        self._execute(
            f"""INSERT OR IGNORE INTO msg_message_status (id, message_id, user_id, status, timestamp)
                SELECT 
                    ? + m.id % 1000000,
                    m.id,
                    ?,
                    ?,
                    ?
                FROM msg_messages m
                WHERE m.id IN {in_clause}
                AND NOT EXISTS (
                    SELECT 1 FROM msg_message_status s 
                    WHERE s.message_id = m.id AND s.user_id = ?
                )""",
            (status_id, user_id, MessageStatusType.DELIVERED.value, timestamp) + params + (user_id,),
            auto_commit=auto_commit,
        )

        return len(message_ids)

    def batch_mark_read(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        up_to_message_id: Optional[SnowflakeID],
        timestamp: int,
        status_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> int:
        """Mark messages as read in batch."""
        # 1. Find all messages in the conversation that need marking (not by user, not deleted)
        query = "SELECT id FROM msg_messages WHERE conversation_id = ? AND author_id != ? AND deleted = 0"
        params: List[Any] = [conversation_id, user_id]
        
        if up_to_message_id:
            query += " AND id <= ?"
            params.append(up_to_message_id)
            
        msg_rows = self._fetch_all(query, tuple(params))
        if not msg_rows:
            return 0
            
        message_ids = [row["id"] for row in msg_rows]
        in_clause, in_params = self._build_in_clause(message_ids)
        
        # 2. Update existing statuses to 'read'
        self._execute(
            f"""UPDATE msg_message_status 
                SET status = ?, timestamp = ?
                WHERE user_id = ? AND status != ?
                AND message_id IN {in_clause}""",
            (MessageStatusType.READ.value, timestamp, user_id, MessageStatusType.READ.value) + in_params,
            auto_commit=False
        )
        
        # 3. Insert new 'read' statuses for messages that don't have ANY status yet
        # We find which IDs already have a status for this user
        existing_rows = self._fetch_all(
            f"SELECT message_id FROM msg_message_status WHERE user_id = ? AND message_id IN {in_clause}",
            (user_id,) + in_params
        )
        existing_ids = {row["message_id"] for row in existing_rows}
        missing_ids = [mid for mid in message_ids if mid not in existing_ids]
        
        if missing_ids:
            # We insert missing ones individually or in a small batch if possible
            # msg_message_status.id is likely a primary key, we need unique values
            for i, mid in enumerate(missing_ids):
                # Use i to ensure unique status IDs if multiple inserts happen in same millisecond
                new_status_id = status_id + (i % 1000000)
                self._execute(
                    """INSERT INTO msg_message_status (id, message_id, user_id, status, timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (new_status_id, mid, user_id, MessageStatusType.READ.value, timestamp),
                    auto_commit=False
                )
        
        if auto_commit:
            self.commit()
            
        return len(message_ids)

    def get_unread_count(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> int:
        """Get unread count for a single conversation."""
        row = self._fetch_one(
            """SELECT COUNT(m.id) as unread_count
               FROM msg_participants p
               LEFT JOIN msg_messages m ON m.conversation_id = p.conversation_id
               WHERE p.user_id = ? AND p.conversation_id = ?
               AND m.author_id != ? AND m.deleted = 0
               AND (p.last_read_message_id IS NULL OR m.id > p.last_read_message_id)""",
            (user_id, conversation_id, user_id),
        )
        return row["unread_count"] if row else 0

    def get_all_unread_counts(
        self, user_id: SnowflakeID
    ) -> Dict[SnowflakeID, int]:
        """Get unread counts for all user's conversations."""
        rows = self._fetch_all(
            """SELECT p.conversation_id, COUNT(m.id) as unread_count
               FROM msg_participants p
               LEFT JOIN msg_messages m ON m.conversation_id = p.conversation_id
               WHERE p.user_id = ? 
               AND m.author_id != ? AND m.deleted = 0
               AND (p.last_read_message_id IS NULL OR m.id > p.last_read_message_id)
               GROUP BY p.conversation_id""",
            (user_id, user_id),
        )
        return {row["conversation_id"]: row["unread_count"] for row in rows}

    def get_reader_ids(self, message_id: SnowflakeID) -> List[SnowflakeID]:
        """Get IDs of users who have read a message."""
        rows = self._fetch_all(
            "SELECT user_id FROM msg_message_status WHERE message_id = ? AND status = 'read' ORDER BY timestamp ASC",
            (message_id,),
        )
        return [row["user_id"] for row in rows]

    def get_batch_reader_ids(self, message_ids: List[SnowflakeID]) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Get IDs of users who have read messages (batch)."""
        if not message_ids:
            return {}
            
        in_clause, params = self._build_in_clause(message_ids)
        rows = self._fetch_all(
            f"SELECT message_id, user_id FROM msg_message_status WHERE message_id IN {in_clause} AND status = 'read' ORDER BY timestamp ASC",
            params,
        )
        
        result: Dict[SnowflakeID, List[SnowflakeID]] = {mid: [] for mid in message_ids}
        for row in rows:
            result[row["message_id"]].append(row["user_id"])
        return result

    def row_to_model(self, row: Dict[str, Any]) -> MessageStatus:
        """Convert database row to MessageStatus model."""
        return MessageStatus(
            id=row["id"],
            message_id=row["message_id"],
            user_id=row["user_id"],
            status=MessageStatusType(row["status"]),
            timestamp=row["timestamp"],
        )
