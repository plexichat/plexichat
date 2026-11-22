from typing import Dict, List, Optional, Set

from src.core.base import SnowflakeID
from src.core.servers.permission_utils import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
)

from ..exceptions import PermissionDeniedError
from .protocol import ServerProtocol


# Bound the ``user_id IN (...)`` fan-out of a bulk channel-view resolution. A
# role/@everyone mention can hand tens of thousands of ids to the notification
# fan-out; chunking keeps every statement under the backend bind-variable cap
# and the result sets short-lived.
_CHANNEL_VIEW_BULK_CHUNK_SIZE = 500


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
            is_owner[sid] = type(row["owner_id"]) is int and row["owner_id"] == int(
                user_id
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

    def get_channel_view_user_ids(
        self,
        user_ids: List[SnowflakeID],
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> Set[SnowflakeID]:
        """Resolve ``channels.view`` for many users in one server/channel.

        A notification/audience fan-out asks the same
        ``has_permission(user, server, 'channels.view', channel)`` question for
        every recipient. On a cold permission snapshot each of those checks
        re-reads the server owner, the member row, the member's roles and the
        channel's role/member overrides -- an N+1 that grows linearly with the
        audience size. This method resolves the exact same verdict for the whole
        set with a fixed number of set-based queries per bounded chunk.

        Parity with the per-user resolver (``get_permissions`` +
        ``check_permission``) is deliberate: the same tables, the same
        ``calculate_base_permissions`` / ``apply_channel_overrides`` helpers,
        the same lowest-position-first ordering of role overrides, and the same
        owner/member/administrator shortcuts. Only the *batching* differs. The
        result is keyed strictly by the ids passed in, so a recipient can never
        pick up another user's verdict.

        Fail-closed: a user whose membership cannot be verified, or whose
        verdict raises, is omitted (never added on error). A deleted/unknown
        server yields an empty set.
        """
        result: Set[SnowflakeID] = set()
        ids: List[int] = []
        for uid in user_ids:
            try:
                key = int(uid)
            except (TypeError, ValueError):
                continue
            ids.append(key)
        ids = list(dict.fromkeys(ids))
        if not ids:
            return result

        server_row = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if not server_row:
            return result
        try:
            owner_id = int(server_row["owner_id"])
        except (KeyError, TypeError, ValueError):
            return result

        for start in range(0, len(ids), _CHANNEL_VIEW_BULK_CHUNK_SIZE):
            chunk = ids[start : start + _CHANNEL_VIEW_BULK_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)

            member_rows = (
                self._db.fetch_all(
                    "SELECT id, user_id FROM srv_members "
                    f"WHERE server_id = ? AND user_id IN ({placeholders})",
                    (server_id, *chunk),
                )
                or []
            )
            members: Dict[int, bool] = {}
            member_user_by_id: Dict[int, int] = {}
            for row in member_rows:
                try:
                    uid = int(row["user_id"])
                    mid = int(row["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                members[uid] = True
                member_user_by_id[mid] = uid

            # One set-based join for every member's roles. ``r.*`` carries the
            # permissions/position fields the base-permission and override
            # helpers need; srv_roles.deleted filtering matches the per-user
            # ``_get_member_role_rows`` resolver exactly.
            roles_by_user: Dict[int, List[dict]] = {uid: [] for uid in chunk}
            member_ids = list(member_user_by_id)
            if member_ids:
                mph = ",".join("?" for _ in member_ids)
                role_rows = (
                    self._db.fetch_all(
                        "SELECT r.*, mr.member_id FROM srv_roles r "
                        "INNER JOIN srv_member_roles mr ON r.id = mr.role_id "
                        f"WHERE mr.member_id IN ({mph}) AND r.deleted = 0",
                        tuple(member_ids),
                    )
                    or []
                )
                for row in role_rows:
                    try:
                        uid = member_user_by_id.get(int(row["member_id"]))
                    except (KeyError, TypeError, ValueError):
                        uid = None
                    if uid is not None:
                        roles_by_user[uid].append(dict(row))

            # Two bounded queries for all channel overrides that could affect
            # this chunk: role-targeted (for the roles those members hold) and
            # member-targeted (for the recipients themselves).
            role_overrides_by_role: Dict[int, List[dict]] = {}
            all_role_ids = list(
                dict.fromkeys(
                    int(r["id"])
                    for rows in roles_by_user.values()
                    for r in rows
                    if "id" in r
                )
            )
            for rstart in range(0, len(all_role_ids), _CHANNEL_VIEW_BULK_CHUNK_SIZE):
                role_chunk = all_role_ids[
                    rstart : rstart + _CHANNEL_VIEW_BULK_CHUNK_SIZE
                ]
                rph = ",".join("?" for _ in role_chunk)
                role_override_rows = (
                    self._db.fetch_all(
                        "SELECT * FROM srv_channel_overrides "
                        f"WHERE channel_id = ? AND target_type = 'role' AND target_id IN ({rph})",
                        (channel_id, *role_chunk),
                    )
                    or []
                )
                for row in role_override_rows:
                    try:
                        role_overrides_by_role.setdefault(
                            int(row["target_id"]), []
                        ).append(dict(row))
                    except (KeyError, TypeError, ValueError):
                        continue

            member_overrides_by_user: Dict[int, dict] = {}
            member_override_rows = (
                self._db.fetch_all(
                    "SELECT * FROM srv_channel_overrides "
                    f"WHERE channel_id = ? AND target_type = 'member' AND target_id IN ({placeholders})",
                    (channel_id, *chunk),
                )
                or []
            )
            for row in member_override_rows:
                try:
                    member_overrides_by_user[int(row["target_id"])] = dict(row)
                except (KeyError, TypeError, ValueError):
                    continue

            for uid in chunk:
                try:
                    is_owner = uid == owner_id
                    if uid not in members and not is_owner:
                        # Non-member, non-owner: fail closed, exactly as the
                        # per-user resolver returns {} before any override.
                        continue
                    role_rows = roles_by_user.get(uid, [])
                    base_perms = calculate_base_permissions(role_rows, is_owner)

                    role_positions = {
                        int(r["id"]): int(r.get("position") or 0) for r in role_rows
                    }
                    role_overrides: List[dict] = []
                    for role_row in role_rows:
                        role_overrides.extend(
                            role_overrides_by_role.get(int(role_row["id"]), [])
                        )
                    # Lowest position first so a higher role's deny wins -- the
                    # same ordering the per-user resolver applies.
                    role_overrides.sort(
                        key=lambda o: role_positions.get(
                            int(o.get("target_id") or 0), 0
                        )
                    )

                    perms = apply_channel_overrides(
                        base_perms, role_overrides, member_overrides_by_user.get(uid)
                    )
                    if check_permission(perms, "channels.view"):
                        result.add(uid)
                except Exception:
                    # Fail closed for this recipient only; never invent a grant.
                    continue

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
