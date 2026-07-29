"""
Servers collector for srv_members, srv_onboarding_progress tables.

Collects server memberships and onboarding progress.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ServersCollector(BaseCollector):
    """Collects server membership and onboarding data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect server memberships and onboarding progress."""
        return {
            "server_memberships": self._collect_memberships(user_id),
            "onboarding_progress": self._collect_onboarding(user_id),
        }

    def _collect_memberships(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect srv_members."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT sm.id, sm.server_id, sm.nickname, sm.joined_at, sm.roles
                FROM srv_members sm WHERE sm.user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect server memberships for user {user_id}: {e}"
            )
            return []

    def _collect_onboarding(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect srv_onboarding_progress."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM srv_onboarding_progress WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect onboarding for user {user_id}: {e}")
            return []
