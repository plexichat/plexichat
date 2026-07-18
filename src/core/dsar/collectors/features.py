"""
Features collector for user_features, user_feature_usage, user_features_audit tables.

Collects feature flags, usage statistics, and audit logs.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class FeaturesCollector(BaseCollector):
    """Collects feature flag data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect features, usage, and audit logs."""
        return {
            "features": self._collect_table("user_features", user_id),
            "feature_usage": self._collect_table("user_feature_usage", user_id),
            "features_audit": self._collect_table("user_features_audit", user_id),
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
