"""
Polls collector for poll_votes and poll_polls tables.

Collects poll votes by user and polls created by user.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class PollsCollector(BaseCollector):
    """Collects poll votes and created polls."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect poll votes and created polls."""
        return {
            "poll_votes": self._collect_votes(user_id),
            "created_polls": self._collect_polls(user_id),
        }

    def _collect_votes(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect poll_votes."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect poll votes for user {user_id}: {e}")
            return []

    def _collect_polls(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect poll_polls created by user."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_polls WHERE creator_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect polls for user {user_id}: {e}")
            return []
