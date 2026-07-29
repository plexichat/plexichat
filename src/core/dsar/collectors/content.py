"""
Content collector for msg_pinned, react_reactions, msg_attachments tables.

Collects pinned messages, reactions, and attachment metadata.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ContentCollector(BaseCollector):
    """Collects content interaction data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect pinned messages, reactions, and attachments."""
        return {
            "pinned_messages": self._collect_pinned(user_id),
            "reactions": self._collect_reactions(user_id),
            "attachments": self._collect_attachments(user_id),
        }

    def _collect_pinned(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_pinned."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_pinned WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect pinned for user {user_id}: {e}")
            return []

    def _collect_reactions(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect react_reactions."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM react_reactions WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect reactions for user {user_id}: {e}")
            return []

    def _collect_attachments(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_attachments metadata."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, message_id, file_name, file_type, file_size,
                       width, height, duration, created_at
                FROM msg_attachments WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect attachments for user {user_id}: {e}")
            return []
