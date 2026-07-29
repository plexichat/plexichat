"""
Automod collector for automod_violations, automod_reputation, automod_exemptions tables.

Collects automod violations, reputation scores, and exemptions.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class AutomodCollector(BaseCollector):
    """Collects automod data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect automod violations, reputation, and exemptions."""
        return {
            "violations": self._collect_table("automod_violations", user_id),
            "reputation": self._collect_table("automod_reputation", user_id),
            "exemptions": self._collect_table("automod_exemptions", user_id),
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
