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
        """Collect srv_members with each membership's assigned role ids.

        ``srv_members`` has no ``roles`` column; roles are a many-to-many via
        ``srv_member_roles`` (member_id -> role_id). Selecting ``sm.roles`` made
        SQLite raise "no such column" and the except branch returned [], so
        every DSAR export reported zero server memberships.

        The role ids used to be gathered with one ``srv_member_roles`` query
        *per membership* (a classic N+1 that grew linearly with the number of
        servers the subject had joined). Both halves are now subject-scoped and
        the role ids are grouped in memory, so the query budget is constant.
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT sm.id, sm.server_id, sm.user_id, sm.nickname,
                       sm.joined_at, sm.updated_at, sm.muted, sm.deafened,
                       sm.inviter_id, sm.timeout_until, sm.is_temporary
                FROM srv_members sm WHERE sm.user_id = ?
                """,
                (user_id,),
            )
            memberships = [dict(row) for row in rows]
            roles_by_member = self._collect_member_roles_bulk(user_id)
            for membership in memberships:
                membership["role_ids"] = roles_by_member.get(membership["id"], [])
            return memberships
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect server memberships for user {user_id}: {e}"
            )
            return []

    def _collect_member_roles_bulk(self, user_id: int) -> Dict[int, List[Any]]:
        """Collect every role id for the subject's memberships in one query.

        The join is anchored on ``srv_members.user_id`` so a role row can never
        be pulled in for a membership that does not belong to the subject.
        A failure degrades to "no roles" for all memberships - matching the old
        per-member behaviour - instead of aborting the whole category.
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT mr.member_id, mr.role_id
                FROM srv_member_roles mr
                JOIN srv_members sm ON sm.id = mr.member_id
                WHERE sm.user_id = ?
                ORDER BY mr.member_id, mr.id
                """,
                (user_id,),
            )
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect member roles for user {user_id}: {e}")
            return {}

        grouped: Dict[int, List[Any]] = {}
        for row in rows:
            grouped.setdefault(row["member_id"], []).append(row["role_id"])
        return grouped

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
