"""
Friends operations mixin for the RelationshipManager.
"""

from typing import Dict, List

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_get_many, cache_set, cached

from ..models import Friend
from ..exceptions import (
    NotFriendsError,
    SelfRelationshipError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


# Chunk size for the bulk per-user friend lookup. Each execution binds at most
# this many ids to the single ``user_id IN (...)`` predicate, staying below
# SQLite's variable ceiling and preventing an unbounded IN clause from a large
# fan-out (group membership, presence audiences, notification batches).
_BULK_FRIEND_ID_CHUNK_SIZE = 500

# Deliberately the SAME prefix/TTL/key shape as the cached single-user
# ``get_friend_ids``. Sharing the key means every existing send/accept/remove/
# block invalidation keeps working, so the bulk path can never serve a stale
# friendship decision the single lookup would not also serve.
_FRIEND_IDS_CACHE_PREFIX = "user_friends"
_FRIEND_IDS_CACHE_TTL = 300


def _friend_id_chunks(items: List[int]):
    """Yield bounded slices of ``items`` for IN-clause binding."""
    for start in range(0, len(items), _BULK_FRIEND_ID_CHUNK_SIZE):
        yield items[start : start + _BULK_FRIEND_ID_CHUNK_SIZE]


class FriendsMixin(RelationshipHelpersMixin, RelationshipMixinProtocol):
    """Mixin providing friends operations."""

    def get_friends(self, user_id: SnowflakeID, limit: int = 100) -> List[Friend]:
        """Get list of friends for a user."""
        # Clamp on BOTH sides: SQLite treats LIMIT -1 as *unlimited*, while an
        # arbitrarily large positive limit would still dump the whole friend list in
        # one read. The shared helper keeps every relationship list bounded.
        limit = self._bounded_list_limit(limit)
        rows = self._db.fetch_all(
            """SELECT * FROM rel_friends
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [self._row_to_friend(row) for row in rows]

    @cached(ttl=300, prefix="user_friends")
    def get_friend_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Get list of friend user IDs."""
        rows = self._db.fetch_all(
            "SELECT friend_id FROM rel_friends WHERE user_id = ?", (user_id,)
        )
        return [row["friend_id"] for row in rows]

    def get_friend_ids_bulk(
        self, user_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Resolve the friend set for many users in bounded batches.

        Mirrors :meth:`get_friend_ids` but collapses an N-per-id fan-out (e.g.
        hydration of a presence/DM audience) into one bulk cache read plus at
        most ``ceil(len(ids) / 500)`` DB round trips.

        Results are identical to calling ``get_friend_ids`` once per id,
        including fail-closed handling of ids with no friend rows (an empty
        set, never "everyone is a friend"). Every requested id is present in
        the mapping, so callers can index it directly. A failed authoritative
        lookup raises instead of returning a partial answer: callers must fail
        closed (deny) rather than treat an unknown friendship as a grant.
        """
        ids = list(dict.fromkeys(int(uid) for uid in user_ids))
        if not ids:
            return {}

        result: Dict[SnowflakeID, List[SnowflakeID]] = {uid: [] for uid in ids}

        # Warm path: one bulk cache read for every per-user set. A cached empty
        # list is a legitimate hit and is returned as such (not a DB miss).
        keys = {f"{_FRIEND_IDS_CACHE_PREFIX}:int:{uid}": uid for uid in ids}
        try:
            cached_values = cache_get_many(list(keys))
        except Exception as cache_error:  # a cache outage must never fail open
            logger.debug(f"bulk friend-id cache read failed: {cache_error}")
            cached_values = {}

        misses: List[int] = []
        for key, uid in keys.items():
            value = cached_values.get(key)
            if not isinstance(value, (list, tuple)):
                misses.append(uid)
                continue
            try:
                result[uid] = [int(item) for item in value]
            except (TypeError, ValueError):
                # Malformed cache entry: re-resolve from the authoritative DB.
                misses.append(uid)

        if not misses:
            return result

        # Cold path: chunk the IN binds. Only rows whose user_id belongs to the
        # chunk are accepted, so a stray/malformed row can never merge one
        # user's friendships into another user's set.
        for chunk in _friend_id_chunks(misses):
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._db.fetch_all(
                f"SELECT user_id, friend_id FROM rel_friends "
                f"WHERE user_id IN ({placeholders})",
                tuple(chunk),
            )
            friends_for: Dict[int, List[int]] = {uid: [] for uid in chunk}
            for row in rows:
                uid = int(row["user_id"])
                if uid in friends_for:
                    friends_for[uid].append(int(row["friend_id"]))
            for uid, values in friends_for.items():
                result[uid] = values
                try:
                    cache_set(
                        f"{_FRIEND_IDS_CACHE_PREFIX}:int:{uid}",
                        values,
                        ttl=_FRIEND_IDS_CACHE_TTL,
                    )
                except Exception as cache_error:
                    # The authoritative answer is already in ``result``; a
                    # cache write failure must not drop the whole batch.
                    logger.debug(
                        f"bulk friend-id cache write failed for {uid}: {cache_error}"
                    )

        return result

    def remove_friend(self, user_id: SnowflakeID, friend_id: SnowflakeID) -> bool:
        """Remove a friend (unfriend)."""
        if user_id == friend_id:
            raise SelfRelationshipError("Cannot unfriend yourself")
        if not self._are_friends(user_id, friend_id):
            raise NotFriendsError("You are not friends with this user")

        def delete_friendship() -> None:
            self._db.execute(
                "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                (user_id, friend_id),
            )
            self._db.execute(
                "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                (friend_id, user_id),
            )

        self._run_in_transaction(delete_friendship)
        invalidate_friend_ids = getattr(self.get_friend_ids, "invalidate", None)
        if callable(invalidate_friend_ids):
            invalidate_friend_ids(self, user_id)
            invalidate_friend_ids(self, friend_id)
        self._invalidate_all_relationships_cache(user_id, friend_id)

        logger.debug(f"Friendship removed between {user_id} and {friend_id}")
        return True
