from typing import Optional, List

from src.core.base import SnowflakeID
from src.core.database import cache_delete, invalidate_pattern
from src.core.database.cache import cached

from ..models import Member, AuditLogAction
from ..exceptions import (
    ServerNotFoundError,
    OwnerCannotLeaveError,
    MemberNotFoundError,
)
from .protocol import ServerProtocol


class MemberOpsMixin(ServerProtocol):
    """Mixin for member operations."""

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        return row is not None

    @cached(ttl=30, prefix="server_members")
    def get_members(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 100,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Member]:
        if not self._is_member(server_id, user_id):
            from ..exceptions import ServerAccessDeniedError

            raise ServerAccessDeniedError("Not a member of this server")

        limit = min(limit, 999)

        query = "SELECT * FROM srv_members WHERE server_id = ?"
        params: List[SnowflakeID | int] = [server_id]

        if after_id:
            query += " AND id > ?"
            params.append(after_id)

        query += " ORDER BY joined_at LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))
        if not rows:
            return []

        member_ids = [row["id"] for row in rows]
        if not member_ids:
            return []

        placeholders = ",".join(["?"] * len(member_ids))
        role_rows = self._db.fetch_all(
            f"SELECT member_id, role_id FROM srv_member_roles WHERE member_id IN ({placeholders})",
            tuple(member_ids),
        )

        roles_map = {}
        for rr in role_rows:
            mid = rr["member_id"]
            if mid not in roles_map:
                roles_map[mid] = []
            roles_map[mid].append(rr["role_id"])

        from .converters import _row_to_member

        return [_row_to_member(row, roles=roles_map.get(row["id"], [])) for row in rows]

    def get_member_user_ids(
        self,
        server_id: SnowflakeID,
        exclude_user_id: Optional[SnowflakeID] = None,
    ) -> List[SnowflakeID]:
        query = "SELECT user_id FROM srv_members WHERE server_id = ?"
        params: List[SnowflakeID] = [server_id]

        if exclude_user_id:
            query += " AND user_id != ?"
            params.append(exclude_user_id)

        rows = self._db.fetch_all(query, tuple(params))
        return [row["user_id"] for row in rows]

    def get_all_shared_member_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        rows = self._db.fetch_all(
            """SELECT DISTINCT m2.user_id
               FROM srv_members m1
               JOIN srv_members m2 ON m1.server_id = m2.server_id
               WHERE m1.user_id = ? AND m2.user_id != ?""",
            (user_id, user_id),
        )
        return [row["user_id"] for row in rows]

    def is_timed_out(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        member = self.get_member(server_id, user_id)
        if not member or not member.timeout_until:
            return False

        return member.timeout_until > self._get_timestamp()

    def remove_member(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        if server.owner_id == user_id:
            raise OwnerCannotLeaveError(
                "Owner cannot leave. Transfer ownership first or delete the server."
            )

        member = self.get_member(server_id, user_id)
        if not member:
            raise MemberNotFoundError("Not a member of this server")

        self._db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ?",
            (member.id,),
        )

        self._db.execute(
            "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )

        if self._messaging:
            try:
                channels = self._db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND deleted = 0",
                    (server_id,),
                )
                conv_ids = [
                    ch["conversation_id"] for ch in channels if ch["conversation_id"]
                ]

                if conv_ids:
                    if hasattr(
                        self._messaging, "remove_participant_from_conversations"
                    ):
                        self._messaging.remove_participant_from_conversations(
                            user_id, conv_ids
                        )
            except Exception as e:
                import utils.logger as logger

                logger.error(
                    f"Error removing member {user_id} from server conversations: {e}"
                )

        self._cache_invalidate(self._member_cache_prefix, f"{server_id}:{user_id}")
        self._cache_invalidate(
            self._member_cache_prefix, f"is_member:{server_id}:{user_id}"
        )
        self._cache_invalidate(
            self._member_roles_cache_prefix, f"{server_id}:{user_id}"
        )
        self._cache_invalidate(self._permission_cache_prefix, f"{user_id}:{server_id}:")

        cache_delete(f"is_member:{server_id}:{user_id}")
        invalidate_pattern(f"perms:{user_id}:{server_id}:*")
        invalidate_pattern(f"member_data:*{user_id}*")
        cache_delete(f"user_servers:{user_id}")

        invalidate_pattern(f"srv_permission:{user_id}:{server_id}:*")

        self._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_LEAVE, "member", user_id
        )

        return True
