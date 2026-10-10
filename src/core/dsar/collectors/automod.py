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
            "exemptions": self._collect_exemptions(user_id),
        }

    def _collect_exemptions(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect automod_exemptions granted to a user.

        automod_exemptions has no `user_id`: an exemption targets either a user
        or a role, recorded in `target_type` ('user' / 'role') and `target_id`.
        Routing it through the generic `user_id` predicate raised "no such
        column" and the export omitted exemptions entirely.
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT * FROM automod_exemptions
                WHERE target_type = 'user' AND target_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect automod_exemptions for user {user_id}: {e}"
            )
            return []

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
