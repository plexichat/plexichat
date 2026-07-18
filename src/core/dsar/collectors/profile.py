"""
Profile collector for user_profiles, user_settings, msg_content_filters,
msg_user_settings, pres_custom_status, pres_activity tables.

Collects profile data, user settings, content filters, message settings,
custom status, and activity data.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ProfileCollector(BaseCollector):
    """Collects profile and settings data from multiple tables."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect all profile-related data."""
        return {
            "profiles": self._collect_table("user_profiles", "user_id = ?", (user_id,)),
            "settings": self._collect_table("user_settings", "user_id = ?", (user_id,)),
            "content_filters": self._collect_table(
                "msg_content_filters", "user_id = ?", (user_id,)
            ),
            "msg_settings": self._collect_table(
                "msg_user_settings", "user_id = ?", (user_id,)
            ),
            "custom_status": self._collect_table(
                "pres_custom_status", "user_id = ?", (user_id,)
            ),
            "activity": self._collect_table("pres_activity", "user_id = ?", (user_id,)),
        }

    def _collect_table(
        self, table: str, where: str, params: tuple
    ) -> List[Dict[str, Any]]:
        """Collect all rows from a table."""
        try:
            rows = self._db.fetch_all(f"SELECT * FROM {table} WHERE {where}", params)
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect {table} for user {params[0]}: {e}")
            return []
