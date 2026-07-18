"""
Messages collector for msg_messages, msg_participants, msg_conversations,
msg_forwarded, msg_scheduled, msg_edit_history, user_bookmarks tables.

Collects messages authored by user, conversation participation, conversations
owned, forwarded messages, scheduled messages, edit history, and bookmarks.
Redacts encrypted message content.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class MessagesCollector(BaseCollector):
    """Collects message-related data from multiple tables."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect all message-related data."""
        return {
            "messages": self._collect_messages(user_id),
            "participants": self._collect_participants(user_id),
            "conversations": self._collect_conversations(user_id),
            "forwarded": self._collect_forwarded(user_id),
            "scheduled": self._collect_scheduled(user_id),
            "edit_history": self._collect_edit_history(user_id),
            "bookmarks": self._collect_bookmarks(user_id),
        }

    def _collect_messages(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect messages authored by user."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, conversation_id, author_id, content, created_at,
                       edited_at, deleted_at, is_forwarded, is_scheduled
                FROM msg_messages WHERE author_id = ?
                """,
                (user_id,),
            )
            result = []
            for row in rows:
                r = dict(row)
                if r.get("content", "").startswith("ENC:"):
                    r["content"] = "(encrypted)"
                result.append(r)
            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect messages for user {user_id}: {e}")
            return []

    def _collect_participants(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_participants."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT mp.id, mp.conversation_id, mp.user_id, mp.joined_at,
                       mp.left_at, mp.nick, mp.is_owner, mp.is_muted
                FROM msg_participants mp WHERE mp.user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect participants for user {user_id}: {e}")
            return []

    def _collect_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_conversations owned by user."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_conversations WHERE owner_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect conversations for user {user_id}: {e}")
            return []

    def _collect_forwarded(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_forwarded where user is original author."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_forwarded WHERE original_author_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect forwarded for user {user_id}: {e}")
            return []

    def _collect_scheduled(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_scheduled authored by user."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_scheduled WHERE author_id = ?", (user_id,)
            )
            result = []
            for row in rows:
                r = dict(row)
                if r.get("content", "").startswith("ENC:"):
                    r["content"] = "(encrypted)"
                result.append(r)
            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect scheduled for user {user_id}: {e}")
            return []

    def _collect_edit_history(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_edit_history where user is editor."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_edit_history WHERE editor_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect edit history for user {user_id}: {e}")
            return []

    def _collect_bookmarks(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect user_bookmarks."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_bookmarks WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect bookmarks for user {user_id}: {e}")
            return []
