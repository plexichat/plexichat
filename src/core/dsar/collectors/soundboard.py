"""
Soundboard collector for soundboard_usage table.

Collects soundboard usage data.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class SoundboardCollector(BaseCollector):
    """Collects soundboard usage data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect soundboard usage."""
        return {"soundboard_usage": self._collect_table("soundboard_usage", user_id)}

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
