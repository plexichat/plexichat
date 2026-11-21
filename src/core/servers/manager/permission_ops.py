from typing import Dict, List, Optional

from src.core.base import SnowflakeID
from src.core.servers.permission_utils import calculate_base_permissions

from ..exceptions import PermissionDeniedError
from .protocol import ServerProtocol


class PermissionOpsMixin(ServerProtocol):
    """Mixin for permission operations."""

    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return self.role_handler.has_permission(
            user_id, server_id, permission, channel_id
        )

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        return self.role_handler.get_permissions(user_id, server_id, channel_id)

    def get_permissions_bulk(
        self,
        user_id: SnowflakeID,
        server_ids: List[SnowflakeID],
    ) -> Dict[SnowflakeID, Dict[str, bool]]:
        """Resolve base permissions for one user across many servers at once.

        Callers that previously looped over ``get_permissions`` once per server
        paid an owner lookup, a member lookup and a role lookup on every cold
        cache read (an N+1 that grew with the user's server count). This method
        resolves the same channel-less permission set with two set-based
        queries regardless of how many servers are asked for.

        Parity with ``get_permissions(user, server, channel_id=None)``: an
        owner or a role with ``administrator`` gets every permission, otherwise
        the assigned roles' permission bits are OR-ed together via the shared
        ``calculate_base_permissions`` primitive. Channel overrides cannot
        apply (channel_id is None) and timeouts only revoke interaction
        permissions, never the server-scoped bits such as
        ``server.view_audit_log``. Servers the user is not a member of are
        excluded, matching ``get_servers``' membership precondition.

        The result is keyed by the integer server id, so a caller can only ever
        see permissions for the servers it passed in.
        """
        result: Dict[SnowflakeID, Dict[str, bool]] = {}
        if not server_ids:
            return result

        unique_ids = list(dict.fromkeys(int(sid) for sid in server_ids))
        placeholders = ",".join("?" for _ in unique_ids)

        # One query: owner id + member id for every candidate server. The IN
        # list is built from bound-parameter count, never from untrusted text.
        base_rows = self._db.fetch_all(
            "SELECT s.id AS server_id, s.owner_id, m.id AS member_id "
            "FROM srv_servers s "
            "JOIN srv_members m ON m.server_id = s.id AND m.user_id = ? "
            f"WHERE s.deleted = 0 AND s.id IN ({placeholders})",
            (user_id, *unique_ids),
        )
        if not base_rows:
            return result

        member_ids: Dict[int, int] = {}
        is_owner: Dict[int, bool] = {}
        for row in base_rows:
            sid = int(row["server_id"])
            member_ids[sid] = row["member_id"]
            # SECURITY: a projected srv_servers.owner_id vouches for the
            # caller's ownership only while it is a genuine integer id.
            # Coercing a malformed-but-coercible value with int() ("05"->5,
            # 5.9->5, True->1) would mark a non-owner as owner and grant the
            # full owner permission set. Fail closed (type() rejects bool).
            is_owner[sid] = (
                type(row["owner_id"]) is int
                and row["owner_id"] == int(user_id)
            )

        # One query: every assigned, non-deleted role for those memberships.
        member_ph = ",".join("?" for _ in member_ids)
        role_rows = self._db.fetch_all(
            "SELECT m.server_id AS server_id, r.permissions AS permissions "
            "FROM srv_member_roles mr "
            "JOIN srv_members m ON m.id = mr.member_id "
            "JOIN srv_roles r ON r.id = mr.role_id AND r.deleted = 0 "
            f"WHERE mr.member_id IN ({member_ph})",
            tuple(member_ids.values()),
        )

        roles_by_server: Dict[int, List[Dict[str, object]]] = {
            sid: [] for sid in member_ids
        }
        for row in role_rows:
            sid = int(row["server_id"])
            if sid in roles_by_server:
                roles_by_server[sid].append({"permissions": row["permissions"]})

        for sid in member_ids:
            result[sid] = calculate_base_permissions(
                roles_by_server[sid], is_owner[sid]
            )
        return result

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        if not self.has_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(
                f"Missing required permission: {permission}", permission
            )
