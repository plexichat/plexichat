from typing import Optional, List

from src.core.base import SnowflakeID
from src.core.database.cache import cached

from ..models import Member, AuditLogAction
from ..exceptions import (
    ServerNotFoundError,
    OwnerCannotLeaveError,
    MemberNotFoundError,
    ServerAccessDeniedError,
)
from .protocol import ServerProtocol

# The per-viewer roster page is one of the hottest member reads: presence,
# gateway fan-out, websocket handlers and REST all page the same server roster
# many times in a single request.  Memoise that page briefly, with a key that
# is explicitly partitioned by the requesting credential.
#
# Three invariants shape this cache (pinned by
# tests/servers/test_core_roster_read_cache.py):
#  * the membership gate runs on EVERY call, OUTSIDE and BEFORE the cached
#    loader.  A warm (even poisoned) page can never turn a would-be denial
#    into a 200 for a user who is not a member.
#  * the key carries ``user_id`` as its own bounded segment, so one viewer's
#    page is never replayed to another credential.
#  * every roster-changing mutation drops this namespace immediately through
#    ``CacheOpsMixin._invalidate_roster_caches`` instead of waiting out the
#    short TTL.
_ROSTER_CACHE_PREFIX = "srv_roster"
_ROSTER_CACHE_TTL = 20


def _roster_cache_key(
    _self: "MemberOpsMixin",
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    limit: int = 100,
    after_id: Optional[SnowflakeID] = None,
) -> str:
    """Build a bounded, credential-partitioned key for a roster page.

    The ``@cached`` decorator passes the bound instance first to custom
    builders; ``self`` is intentionally ignored so entries are shared across
    manager instances (the credential lives in ``user_id``, not the object).
    """
    return (
        f"{_ROSTER_CACHE_PREFIX}:{int(user_id)}:{int(server_id)}:"
        f"{int(limit)}:{int(after_id) if after_id else 0}"
    )


class MemberOpsMixin(ServerProtocol):
    """Mixin for member operations."""

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        return row is not None

    def get_members(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 100,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Member]:
        """Return a page of the server roster to an authorized member.

        The membership check is deliberately OUTSIDE the cached loader: it is
        an authorization boundary and must run on every call before any cache
        tier is consulted.  A cached page therefore cannot keep granting roster
        access to a user who has since left or been removed from the server.
        """
        if not self._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")
        return self._get_members_cached(user_id, server_id, limit, after_id)

    @cached(
        ttl=_ROSTER_CACHE_TTL,
        prefix=_ROSTER_CACHE_PREFIX,
        key_builder=_roster_cache_key,
    )
    def _get_members_cached(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int,
        after_id: Optional[SnowflakeID],
    ) -> List[Member]:
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the whole list.
        limit = max(0, min(limit, 999))

        query = "SELECT * FROM srv_members WHERE server_id = ?"
        params: List[SnowflakeID | int] = [server_id]

        if after_id:
            query += " AND id > ?"
            params.append(after_id)

        query += " ORDER BY id LIMIT ?"
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
        """Return the server's member user ids, memoized for the fan-out burst.

        Gateway routing, presence/typing, voice and REST call this for the same
        server many times per event.  A bounded, short-TTL L0 collapses the
        repeated reads; the key carries both the server and the exclude filter
        so one audience can never be served for another.  A miss (or any cache
        error) recomputes from the authoritative ``srv_members`` table, so the
        cache can never synthesize or widen a recipient set.  Every membership
        mutation drops the namespace through the shared funnel.
        """
        from .member_id_cache import (
            _MISS as _MEMBER_IDS_MISS,
            get_cached_member_user_ids,
            prime_member_user_ids,
        )

        cached = get_cached_member_user_ids(server_id, exclude_user_id)
        if cached is not _MEMBER_IDS_MISS:
            return cached

        query = "SELECT user_id FROM srv_members WHERE server_id = ?"
        params: List[SnowflakeID] = [server_id]

        if exclude_user_id:
            query += " AND user_id != ?"
            params.append(exclude_user_id)

        rows = self._db.fetch_all(query, tuple(params))
        result = [row["user_id"] for row in rows]
        prime_member_user_ids(server_id, exclude_user_id, result)
        return result

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

        self._cache_invalidate(
            self._member_roles_cache_prefix, f"{server_id}:{user_id}"
        )
        self._invalidate_member_caches(server_id, user_id)

        self._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_LEAVE, "member", user_id
        )

        return True
