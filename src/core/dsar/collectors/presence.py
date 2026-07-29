"""
Presence collector for pres_presence and pres_typing tables.

Collects presence status and typing indicators.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class PresenceCollector(BaseCollector):
    """Collects presence and typing data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect presence and typing data."""
        return {
            "presence": self._collect_table("pres_presence", user_id),
            "typing": self._collect_table("pres_typing", user_id),
        }

    def _collect_table(self, table: str, user_id: int) -> List[Dict[str, Any]]:
        """Collect all rows from a table for a user."""
        try:
            rows = self._db.fetch_all(
                f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect {table} for user {user_id}: {e}")
            return []
