"""
Message repository - Data access for messages.
"""

from typing import Any, Dict, List, Optional

from src.core.database import cached, invalidate_pattern
from ..models import Message, MessageType
from .base import BaseRepository
from src.core.base import SnowflakeID
from src.utils.encryption import decrypt_message, is_message_encrypted, decrypt_data


class MessageRepository(BaseRepository[Message]):
    """Repository for message data access."""

    def create(
        self,
        msg_id: SnowflakeID,
        conversation_id: SnowflakeID,
        author_id: SnowflakeID,
        content: str,
        message_type: MessageType,
        created_at: int,
        reply_to_id: Optional[SnowflakeID] = None,
        content_encrypted: Optional[str] = None,
        content_index: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        webhook_id: Optional[SnowflakeID] = None,
        auto_commit: bool = True,
    ) -> None:
        """Create a new message."""
        self._execute(
            """INSERT INTO msg_messages 
               (id, conversation_id, author_id, content, content_encrypted, content_index, message_type, 
                created_at, updated_at, reply_to_id, metadata, webhook_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                author_id,
                content,
                content_encrypted,
                content_index,
                message_type.value,
                created_at,
                created_at,
                reply_to_id,
                self._json_dumps(metadata),
                webhook_id,
            ),
            auto_commit=auto_commit,
        )
        self.invalidate_conversation_cache(conversation_id)

    def get_by_id(self, msg_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get message by ID."""
        return self._fetch_one(
            "SELECT * FROM msg_messages WHERE id = ?",
            (msg_id,),
        )

    def get_batch_by_ids(self, msg_ids: List[SnowflakeID]) -> List[Dict[str, Any]]:
        """Get multiple messages by ID."""
        if not msg_ids:
            return []
        in_clause, params = self._build_in_clause(msg_ids)
        return self._fetch_all(
            f"SELECT * FROM msg_messages WHERE id IN {in_clause}",
            params,
        )

    @cached(ttl=60, prefix="messages_list")
    def get_by_conversation(
        self,
        conversation_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Dict[str, Any]]:
        """Get messages from a conversation with cursor pagination."""
        query = "SELECT * FROM msg_messages WHERE conversation_id = ? AND deleted = 0"
        params: List[Any] = [conversation_id]

        if before_id:
            query += " AND id < ?"
            params.append(before_id)
            query += " ORDER BY id DESC"
        elif after_id:
            query += " AND id > ?"
            params.append(after_id)
            query += " ORDER BY id ASC"
        else:
            query += " ORDER BY id DESC"

        query += " LIMIT ?"
        params.append(limit)

        return self._fetch_all(query, tuple(params))

    def _get_conv_id(self, msg_id: SnowflakeID) -> Optional[SnowflakeID]:
        """Get conversation ID for a message."""
        row = self._fetch_one("SELECT conversation_id FROM msg_messages WHERE id = ?", (msg_id,))
        return row["conversation_id"] if row else None

    def search(
        self,
        conversation_id: SnowflakeID,
        search_query: str,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Dict[str, Any]]:
        """Search messages in a conversation."""
        # If it looks like an exact match search for a single word, try blind index
        use_blind_index = " " not in search_query.strip()
        
        if use_blind_index:
            from src.utils.encryption import blind_index
            query_index = blind_index(search_query, "message_content")
            query = "SELECT * FROM msg_messages WHERE conversation_id = ? AND deleted = 0 AND (content_index = ? OR content LIKE ?)"
            params: List[Any] = [conversation_id, query_index, f"%{search_query}%"]
        else:
            query = "SELECT * FROM msg_messages WHERE conversation_id = ? AND deleted = 0 AND content LIKE ?"
            params: List[Any] = [conversation_id, f"%{search_query}%"]

        if before_id:
            query += " AND id < ?"
            params.append(before_id)
            query += " ORDER BY id DESC"
        elif after_id:
            query += " AND id > ?"
            params.append(after_id)
            query += " ORDER BY id ASC"
        else:
            query += " ORDER BY id DESC"

        query += " LIMIT ?"
        params.append(limit)

        return self._fetch_all(query, tuple(params))

    def update_content(
        self,
        msg_id: SnowflakeID,
        content: str,
        updated_at: int,
        content_index: Optional[str] = None,
        auto_commit: bool = True,
    ) -> None:
        """Update message content (edit)."""
        cid = self._get_conv_id(msg_id)
        self._execute(
            """UPDATE msg_messages 
               SET content = ?, content_encrypted = NULL, content_index = ?, updated_at = ?, edited = 1
               WHERE id = ?""",
            (content, content_index, updated_at, msg_id),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def update_metadata(
        self,
        msg_id: SnowflakeID,
        metadata: Optional[Dict[str, Any]],
        updated_at: int,
        auto_commit: bool = True,
    ) -> None:
        cid = self._get_conv_id(msg_id)
        self._execute(
            "UPDATE msg_messages SET metadata = ?, updated_at = ? WHERE id = ?",
            (self._json_dumps(metadata), updated_at, msg_id),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def soft_delete(
        self, msg_id: SnowflakeID, deleted_at: int, auto_commit: bool = True
    ) -> None:
        """Soft delete a message."""
        cid = self._get_conv_id(msg_id)
        self._execute(
            "UPDATE msg_messages SET deleted = 1, deleted_at = ?, content = '[deleted]' WHERE id = ?",
            (deleted_at, msg_id),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def hard_delete(self, msg_id: SnowflakeID, auto_commit: bool = True) -> None:
        """Hard delete a message."""
        cid = self._get_conv_id(msg_id)
        self._execute(
            "DELETE FROM msg_messages WHERE id = ?",
            (msg_id,),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def exists_in_conversation(
        self, msg_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Check if message exists in conversation."""
        row = self._fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ? AND deleted = 0",
            (msg_id,),
        )
        return row is not None and row["conversation_id"] == conversation_id

    def get_max_id_in_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Get the maximum message ID in a conversation."""
        row = self._fetch_one(
            "SELECT MAX(id) as max_id FROM msg_messages WHERE conversation_id = ? AND deleted = 0",
            (conversation_id,),
        )
        return row["max_id"] if row else None

    def invalidate_conversation_cache(self, conversation_id: SnowflakeID) -> None:
        """Invalidate all message list caches for a conversation."""
        try:
            # Use wider globs to catch various argument serializations (str:ID vs int:ID)
            # Add leading wildcards to catch keys like 'cache:src.api.routes.messages.get_channel_messages:channel_id:int:...'
            pattern1 = f"*messages_list:*{conversation_id}*"
            pattern2 = f"*messages_api:*{conversation_id}*"
            
            count1 = invalidate_pattern(pattern1)
            count2 = invalidate_pattern(pattern2)
            
            total = count1 + count2
            if total > 0:
                from utils.logger import debug
                debug(f"Invalidated {total} message cache keys for conversation {conversation_id}")
        except Exception as e:
            from utils.logger import warning
            warning(f"Failed to invalidate message cache for {conversation_id}: {e}")

    def row_to_model(
        self,
        row: Dict[str, Any],
        pin_info: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Convert database row to Message model."""
        content = row["content"]

        # Decrypt if encrypted (new format with ENC: prefix)
        if is_message_encrypted(content):
            try:
                content = decrypt_message(content, row["id"])
            except Exception:
                content = "[decryption failed]"
        # Legacy: decrypt from content_encrypted field
        elif row.get("content_encrypted") and content == "[encrypted]":
            try:
                content = decrypt_data(row["content_encrypted"])
            except Exception:
                content = "[decryption failed]"

        metadata = self._json_loads(row["metadata"]) if row.get("metadata") else None
        embeds = []
        if metadata and "embeds" in metadata:
            embeds = metadata["embeds"]

        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            author_id=row["author_id"],
            content=content,
            content_encrypted=row.get("content_encrypted"),
            message_type=MessageType(row.get("message_type", "text")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            edited=bool(row.get("edited", False)),
            edited_at=row.get("edited_at"),
            deleted=bool(row.get("deleted", False)),
            deleted_at=row.get("deleted_at"),
            reply_to_id=row.get("reply_to_id"),
            pinned=pin_info is not None,
            pinned_at=pin_info["pinned_at"] if pin_info else None,
            pinned_by=pin_info["pinned_by"] if pin_info else None,
            metadata=metadata,
            embeds=embeds,
        )
