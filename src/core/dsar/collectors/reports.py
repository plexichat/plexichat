"""
Reports collector for message_reports and user_reports tables.

Collects reports where user is reporter or reported user.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ReportsCollector(BaseCollector):
    """Collects message and user reports."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect reports as reporter and reported user."""
        return {
            "message_reports_as_reporter": self._collect_table(
                "message_reports", "reporter_id = ?", (user_id,)
            ),
            "message_reports_as_reported_user": self._collect_table(
                "message_reports", "reported_user_id = ?", (user_id,)
            ),
            "user_reports_as_reporter": self._collect_table(
                "user_reports", "reporter_id = ?", (user_id,)
            ),
            "user_reports_as_reported_user": self._collect_table(
                "user_reports", "reported_user_id = ?", (user_id,)
            ),
        }

    def _collect_table(
        self, table: str, where: str, params: tuple
    ) -> List[Dict[str, Any]]:
        """Collect all rows from a table with a WHERE clause."""
        try:
            rows = self._db.fetch_all(f"SELECT * FROM {table} WHERE {where}", params)
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect {table} for user {params[0]}: {e}")
            return []
