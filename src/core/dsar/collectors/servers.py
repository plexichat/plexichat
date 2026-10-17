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
            # `srv_members` has no `roles` column; roles are a many-to-many via
            # `srv_member_roles` (member_id -> role_id). Selecting `sm.roles`
            # made SQLite raise "no such column" and the except branch returned
            # [], so every DSAR export reported zero server memberships.
            rows = self._db.fetch_all(
                """
                SELECT sm.id, sm.server_id, sm.user_id, sm.nickname,
                       sm.joined_at, sm.updated_at, sm.muted, sm.deafened,
                       sm.inviter_id, sm.timeout_until, sm.is_temporary
                FROM srv_members sm WHERE sm.user_id = ?
                """,
                (user_id,),
            )
            memberships = []
            for row in rows:
                membership = dict(row)
                membership["role_ids"] = self._collect_member_roles(membership["id"])
                memberships.append(membership)
            return memberships
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect server memberships for user {user_id}: {e}"
            )
            return []

    def _collect_member_roles(self, member_id: int) -> List[Any]:
        """Collect the role ids assigned to one srv_members row."""
        try:
            rows = self._db.fetch_all(
                "SELECT role_id FROM srv_member_roles WHERE member_id = ?",
                (member_id,),
            )
            return [dict(row)["role_id"] for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect roles for member {member_id}: {e}")
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
