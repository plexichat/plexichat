"""
Mutual information operations mixin for the RelationshipManager.
"""

from typing import Any, Dict, List

from src.core.base import SnowflakeID
from src.core.security.hardening import (
    SNOWFLAKE_MAX,
    HardeningViolation,
    bounded_int,
)

from ..models import MutualInfo
from .helpers import RelationshipHelpersMixin


# Chunk size for the bulk shared-server lookup. The viewer id is bound once per
# execution and each chunk binds at most this many target ids to the IN clause,
# so the widest statement carries 1 + 500 = 501 binds (below SQLite's ceiling).
_BULK_SERVER_ID_CHUNK_SIZE = 500

# A direct manager call bypasses the HTTP edge's page-size bound. Keep the
# same 200-row ceiling used by the other listing helpers so a huge positive
# limit cannot materialize every suggestion candidate.
_MAX_SUGGESTIONS_PAGE_SIZE = 200


def _clamp_page_limit(value: Any) -> int:
    """Return a page size in ``[0, _MAX_SUGGESTIONS_PAGE_SIZE]``, failing closed."""
    try:
        number = bounded_int(
            value, "limit", minimum=0, maximum=SNOWFLAKE_MAX, default=0
        )
    except HardeningViolation:
        return 0
    return min(number, _MAX_SUGGESTIONS_PAGE_SIZE)


def _server_id_chunks(items: List[int]):
    """Yield bounded slices of ``items`` for IN-clause binding."""
    for start in range(0, len(items), _BULK_SERVER_ID_CHUNK_SIZE):
        yield items[start : start + _BULK_SERVER_ID_CHUNK_SIZE]


class MutualInfoMixin(RelationshipHelpersMixin):
    """Mixin providing mutual information operations."""

    def get_mutual_friends(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Get list of mutual friend IDs between two users."""
        rows = self._db.fetch_all(
            """SELECT f1.friend_id
               FROM rel_friends f1
               INNER JOIN rel_friends f2 ON f1.friend_id = f2.friend_id
               WHERE f1.user_id = ? AND f2.user_id = ?""",
            (user_id, target_id),
        )
        return [row["friend_id"] for row in rows]

    def get_mutual_friend_count(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> int:
        """Get count of mutual friends between two users."""
        row = self._db.fetch_one(
            """SELECT COUNT(*) as count
               FROM rel_friends f1
               INNER JOIN rel_friends f2 ON f1.friend_id = f2.friend_id
               WHERE f1.user_id = ? AND f2.user_id = ?""",
            (user_id, target_id),
        )
        return row["count"] if row else 0

    def get_mutual_servers(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Get list of mutual server IDs between two users."""
        if not self._servers:
            return []
        rows = self._db.fetch_all(
            """SELECT m1.server_id
               FROM srv_members m1
               INNER JOIN srv_members m2 ON m1.server_id = m2.server_id
               WHERE m1.user_id = ? AND m2.user_id = ?""",
            (user_id, target_id),
        )
        return [row["server_id"] for row in rows]

    def get_mutual_server_count(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> int:
        """Get count of mutual servers between two users."""
        if not self._servers:
            return 0
        row = self._db.fetch_one(
            """SELECT COUNT(*) as count
               FROM srv_members m1
               INNER JOIN srv_members m2 ON m1.server_id = m2.server_id
               WHERE m1.user_id = ? AND m2.user_id = ?""",
            (user_id, target_id),
        )
        return row["count"] if row else 0

    def get_mutual_servers_bulk(
        self, user_id: SnowflakeID, target_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Resolve shared-server sets between one user and many targets.

        Mirrors :meth:`get_mutual_servers` (the server ids both users are
        members of) but replaces the per-target fan-out with bounded, chunked
        ``IN`` queries. Deliberately not cached: server membership changes are
        owned by the servers module and have no invalidation hook into this
        manager, so a cache here could outlive a join/leave and over- or
        under-state a shared server. A fresh batched query is authoritative.

        Fail-closed contract: every requested target is present in the mapping,
        with an empty set when no server is shared (or no servers module is
        wired, matching the single lookup). A failed DB lookup raises rather
        than returning a partial answer, so callers can deny instead of
        treating an unknown target as sharing a server. Rows for targets that
        were not requested are ignored, so one user's memberships can never
        leak into another user's set.
        """
        viewer = int(user_id)
        targets = list(dict.fromkeys(int(tid) for tid in target_ids))
        result: Dict[SnowflakeID, List[SnowflakeID]] = {
            tid: [] for tid in targets
        }
        if not targets or not self._servers:
            return result

        for chunk in _server_id_chunks(targets):
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._db.fetch_all(
                f"""SELECT m1.user_id AS target_id, m1.server_id
                    FROM srv_members m1
                    INNER JOIN srv_members m2 ON m1.server_id = m2.server_id
                    WHERE m2.user_id = ? AND m1.user_id IN ({placeholders})""",
                (viewer, *chunk),
            )
            servers_for: Dict[int, set] = {tid: set() for tid in chunk}
            for row in rows:
                target = int(row["target_id"])
                if target in servers_for:
                    servers_for[target].add(int(row["server_id"]))
            for tid, values in servers_for.items():
                result[tid] = sorted(values)

        return result

    def get_mutual_info(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> MutualInfo:
        """Get all mutual information between two users."""
        mutual_friends = self.get_mutual_friends(user_id, target_id)
        mutual_servers = self.get_mutual_servers(user_id, target_id)
        return MutualInfo(
            mutual_friends=mutual_friends,
            mutual_friend_count=len(mutual_friends),
            mutual_servers=mutual_servers,
            mutual_server_count=len(mutual_servers),
        )

    def get_suggested_friends(
        self, user_id: SnowflakeID, limit: int = 10
    ) -> List[SnowflakeID]:
        """Get friend suggestions based on mutual friends."""
        # Clamp both ends: LIMIT -1 is *unlimited* and a huge positive limit
        # dumped every candidate. Invalid input fails closed.
        limit = _clamp_page_limit(limit)
        rows = self._db.fetch_all(
            """
            SELECT f2.friend_id, COUNT(*) as mutual_count
            FROM rel_friends f1
            JOIN rel_friends f2 ON f1.friend_id = f2.user_id
            WHERE f1.user_id = ?
              AND f2.friend_id != ?
              AND f2.friend_id NOT IN (SELECT friend_id FROM rel_friends WHERE user_id = ?)
              AND f2.friend_id NOT IN (SELECT blocked_id FROM rel_blocked WHERE blocker_id = ?)
              AND f2.friend_id NOT IN (SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?)
              AND f2.friend_id NOT IN (
                  SELECT sender_id FROM rel_friend_requests
                  WHERE recipient_id = ? AND status = 'pending'
                  UNION
                  SELECT recipient_id FROM rel_friend_requests
                  WHERE sender_id = ? AND status = 'pending'
              )
            GROUP BY f2.friend_id
            ORDER BY mutual_count DESC
            LIMIT ?
            """,
            (user_id, user_id, user_id, user_id, user_id, user_id, user_id, limit),
        )
        return [row["friend_id"] for row in rows]
