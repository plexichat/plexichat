"""
Last chat service - Tracks and restores the user's last active conversation.

Provides functionality to save, retrieve, and restore the last conversation
a user was viewing, enabling seamless session continuity across reconnects
and page refreshes.
"""

import time
from typing import Optional, Dict, Any, List

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id
from src.core.database import cache_get, cache_set, cache_delete


class LastChatService:
    """Service for managing user's last active chat state."""

    CACHE_TTL = 86400  # 24 hours
    MAX_HISTORY = 10  # Max recent chats tracked per user

    def __init__(self, db, participant_svc=None):
        self._db = db
        self._participant_svc = participant_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def save_last_chat(
        self,
        user_id: int,
        conversation_id: int,
        last_message_id: Optional[int],
        scroll_position: int = 0,
    ) -> Dict[str, Any]:
        """
        Save the user's last active chat state.

        Args:
            user_id: ID of the user
            conversation_id: ID of the conversation
            last_message_id: ID of the last visible message
            scroll_position: Optional scroll position for restoration

        Returns:
            Saved state record dict
        """
        now = self._get_timestamp()

        # Verify user is a participant
        if self._participant_svc and not self._participant_svc.is_participant(
            conversation_id, user_id
        ):
            raise PermissionError("Not a participant in this conversation")

        # Upsert the last chat record
        existing = self._db.fetch_one(
            "SELECT id FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )

        if existing:
            self._db.execute(
                """UPDATE user_last_chat
                   SET conversation_id = ?, last_message_id = ?, scroll_position = ?,
                       updated_at = ?
                   WHERE user_id = ?""",
                (conversation_id, last_message_id, scroll_position, now, user_id),
            )
        else:
            record_id = self._generate_id()
            self._db.execute(
                """INSERT INTO user_last_chat
                   (id, user_id, conversation_id, last_message_id, scroll_position, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    user_id,
                    conversation_id,
                    last_message_id,
                    scroll_position,
                    now,
                ),
            )

        # Save to Redis with TTL
        cache_key = f"last_chat:{user_id}"
        cache_data = {
            "conversation_id": conversation_id,
            "last_message_id": last_message_id,
            "scroll_position": scroll_position,
            "updated_at": now,
        }
        cache_set(cache_key, cache_data, ttl=self.CACHE_TTL)

        logger.debug(
            f"Saved last chat for user {user_id}: conversation {conversation_id}"
        )
        self._add_to_history(user_id, conversation_id)
        result = self.get_last_chat(user_id)
        return result if result is not None else {}

    def get_last_chat(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get the user's last active chat state.

        Args:
            user_id: ID of the user

        Returns:
            Last chat state dict or None if not found
        """
        # Try cache first
        cache_key = f"last_chat:{user_id}"
        cached = cache_get(cache_key)
        if cached:
            # Verify the conversation still exists and user is still a participant
            conv_id = cached.get("conversation_id")
            if conv_id and self._participant_svc:
                if self._participant_svc.is_participant(conv_id, user_id):
                    return cached
                else:
                    # User was removed from the conversation, invalidate
                    cache_delete(cache_key)
                    return None
            return cached

        # Fall back to database
        row = self._db.fetch_one(
            "SELECT * FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )
        if not row:
            return None

        data = dict(row)

        # Verify user is still a participant
        conv_id = data.get("conversation_id")
        if conv_id and self._participant_svc:
            if not self._participant_svc.is_participant(conv_id, user_id):
                # Clean up stale record
                self._db.execute(
                    "DELETE FROM user_last_chat WHERE user_id = ?",
                    (user_id,),
                )
                return None

        result = {
            "conversation_id": data.get("conversation_id"),
            "last_message_id": data.get("last_message_id"),
            "scroll_position": data.get("scroll_position"),
            "updated_at": data.get("updated_at"),
        }

        # Cache it
        cache_set(cache_key, result, ttl=self.CACHE_TTL)

        return result

    def get_recent_chats(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the user's recent chat history.

        Returns conversations ordered by most recently accessed,
        with the last message preview if available.
        """
        limit = min(limit, self.MAX_HISTORY)

        rows = self._db.fetch_all(
            """SELECT h.conversation_id, h.accessed_at, h.unread_count,
                      c.last_message_at, c.last_message_id
               FROM user_recent_chats h
               LEFT JOIN msg_conversations c ON h.conversation_id = c.id
               WHERE h.user_id = ?
               ORDER BY h.accessed_at DESC
               LIMIT ?""",
            (user_id, limit),
        )

        results = []
        for row in rows:
            data = dict(row)
            # Verify user is still a participant
            conv_id = data.get("conversation_id")
            if conv_id and self._participant_svc:
                if not self._participant_svc.is_participant(conv_id, user_id):
                    continue
            results.append(data)

        return results

    def update_unread_count(
        self, user_id: int, conversation_id: int, count: int
    ) -> None:
        """Update unread message count for a conversation in recent chats."""
        self._db.execute(
            """UPDATE user_recent_chats
               SET unread_count = ?
               WHERE user_id = ? AND conversation_id = ?""",
            (count, user_id, conversation_id),
        )

    def clear_last_chat(self, user_id: int) -> bool:
        """Clear the user's last chat state."""
        self._db.execute(
            "DELETE FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )
        cache_key = f"last_chat:{user_id}"
        cache_delete(cache_key)
        return True

    def _add_to_history(self, user_id: int, conversation_id: int) -> None:
        """Add a conversation to the user's recent chat history."""
        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT id FROM user_recent_chats WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id),
        )

        if existing:
            self._db.execute(
                """UPDATE user_recent_chats
                   SET accessed_at = ?, unread_count = 0
                   WHERE user_id = ? AND conversation_id = ?""",
                (now, user_id, conversation_id),
            )
        else:
            # Check if we need to evict the oldest entry
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM user_recent_chats WHERE user_id = ?",
                (user_id,),
            )
            if count_row and count_row["count"] >= self.MAX_HISTORY:
                # Remove the oldest entry
                self._db.execute(
                    """DELETE FROM user_recent_chats
                       WHERE user_id = ? AND id NOT IN (
                           SELECT id FROM user_recent_chats
                           WHERE user_id = ?
                           ORDER BY accessed_at DESC
                           LIMIT ?
                       )""",
                    (user_id, user_id, self.MAX_HISTORY - 1),
                )

            record_id = self._generate_id()
            self._db.execute(
                """INSERT INTO user_recent_chats
                   (id, user_id, conversation_id, accessed_at, unread_count)
                   VALUES (?, ?, ?, ?, 0)""",
                (record_id, user_id, conversation_id, now),
            )

    def _generate_id(self) -> int:
        return generate_snowflake_id()
