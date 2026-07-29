"""
Notifications collector for notif_notifications, notif_unread, notif_settings,
notif_channel_overrides tables.

Collects notifications, unread counts, settings, and channel overrides.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class NotificationsCollector(BaseCollector):
    """Collects notification data from multiple tables."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect all notification-related data."""
        return {
            "notifications": self._collect_table("notif_notifications", user_id),
            "unread_counts": self._collect_table("notif_unread", user_id),
            "settings": self._collect_table("notif_settings", user_id),
            "channel_overrides": self._collect_table(
                "notif_channel_overrides", user_id
            ),
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
