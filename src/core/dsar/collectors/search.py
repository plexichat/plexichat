"""
Search collector for search_history and saved_searches tables.

Collects search history and saved searches.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class SearchCollector(BaseCollector):
    """Collects search history and saved searches."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect search history and saved searches."""
        return {
            "search_history": self._collect_table("search_history", user_id),
            "saved_searches": self._collect_table("saved_searches", user_id),
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
