"""
Block operations mixin for the RelationshipManager.
"""

from typing import Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_get_many, cache_set, cached

from ..models import BlockedUser
from ..exceptions import (
    AlreadyBlockedError,
    CannotBlockSelfError,
    NotBlockedError,
    RelationshipValidationError,
    UserNotFoundError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


# Chunk size for the bulk two-way block lookup. Each chunk binds this many ids
# to *each* side of the ``blocker_id IN (...) OR blocked_id IN (...)`` predicate,
# so the widest statement carries 2 * 500 = 1000 binds - comfortably under
# SQLite's variable ceiling (the same 500-id chunk the notification and search
# bulk lookups use). Keeping it bounded also stops a group DM with thousands of
# participants from building an unbounded IN clause.
_BULK_BLOCK_ID_CHUNK_SIZE = 500

# These are deliberately the SAME key/TTL the cached single-user
# ``get_all_blocked_ids`` uses. Sharing the key means every existing
# block/unblock invalidation keeps working and the bulk path can never serve a
# stale authorization decision the single lookup would not also serve.
_BLOCKED_IDS_CACHE_PREFIX = "user_blocked_all"
_BLOCKED_IDS_CACHE_TTL = 300


def _block_id_chunks(items: List[int]):
    """Yield bounded slices of ``items`` for IN-clause binding."""
    for start in range(0, len(items), _BULK_BLOCK_ID_CHUNK_SIZE):
        yield items[start : start + _BULK_BLOCK_ID_CHUNK_SIZE]


class BlockingMixin(RelationshipHelpersMixin, RelationshipMixinProtocol):
    """Mixin providing block operations."""

    def block_user(
        self,
        blocker_id: SnowflakeID,
        blocked_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> BlockedUser:
        """Block a user."""
        if blocker_id == blocked_id:
            raise CannotBlockSelfError("Cannot block yourself")
        if not self._user_exists(blocked_id):
            raise UserNotFoundError(f"User {blocked_id} not found")
        if self._is_blocked(blocker_id, blocked_id):
            raise AlreadyBlockedError("User is already blocked")

        block_id = self._generate_id()

        def create_block() -> None:
            now = self._get_timestamp()
            self._db.execute(
                """INSERT INTO rel_blocked (id, blocker_id, blocked_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (block_id, blocker_id, blocked_id, reason, now),
            )
            if self._are_friends(blocker_id, blocked_id):
                self._db.execute(
                    "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                    (blocker_id, blocked_id),
                )
                self._db.execute(
                    "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                    (blocked_id, blocker_id),
                )
            self._db.execute(
                """UPDATE rel_friend_requests SET status = 'declined', updated_at = ?
                   WHERE ((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?))
                   AND status = 'pending'""",
                (now, blocker_id, blocked_id, blocked_id, blocker_id),
            )

        self._run_in_transaction(create_block)
        invalidate_blocked_ids = getattr(self.get_blocked_user_ids, "invalidate", None)
        if callable(invalidate_blocked_ids):
            invalidate_blocked_ids(self, blocker_id)
        invalidate_all_blocked_ids = getattr(
            self.get_all_blocked_ids, "invalidate", None
        )
        if callable(invalidate_all_blocked_ids):
            # get_all_blocked_ids is bidirectional: both parties' cached sets
            # change when a block is created.
            invalidate_all_blocked_ids(self, blocker_id)
            invalidate_all_blocked_ids(self, blocked_id)
        invalidate_friend_ids = getattr(self.get_friend_ids, "invalidate", None)
        if callable(invalidate_friend_ids):
            invalidate_friend_ids(self, blocker_id)
            invalidate_friend_ids(self, blocked_id)
        self._invalidate_all_relationships_cache(blocker_id, blocked_id)
        self._invalidate_profile_read_caches(blocker_id, blocked_id)
        self._invalidate_reaction_view_caches()

        logger.debug(f"User {blocker_id} blocked user {blocked_id}")
        result = self.get_block(block_id)
        if result is None:
            raise RelationshipValidationError("Block record not found after creation")
        return result

    def unblock_user(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """Unblock a user."""
        if not self._is_blocked(blocker_id, blocked_id):
            raise NotBlockedError("User is not blocked")

        def delete_block() -> None:
            self._db.execute(
                "DELETE FROM rel_blocked WHERE blocker_id = ? AND blocked_id = ?",
                (blocker_id, blocked_id),
            )

        self._run_in_transaction(delete_block)
        invalidate_blocked_ids = getattr(self.get_blocked_user_ids, "invalidate", None)
        if callable(invalidate_blocked_ids):
            invalidate_blocked_ids(self, blocker_id)
        invalidate_all_blocked_ids = getattr(
            self.get_all_blocked_ids, "invalidate", None
        )
        if callable(invalidate_all_blocked_ids):
            # Bidirectional cache: both parties' sets change on unblock.
            invalidate_all_blocked_ids(self, blocker_id)
            invalidate_all_blocked_ids(self, blocked_id)
        self._invalidate_all_relationships_cache(blocker_id, blocked_id)
        self._invalidate_profile_read_caches(blocker_id, blocked_id)
        self._invalidate_reaction_view_caches()

        logger.debug(f"User {blocker_id} unblocked user {blocked_id}")
        return True

    @staticmethod
    def _invalidate_profile_read_caches(*user_ids: SnowflakeID) -> None:
        """Drop cached profile metadata for every party to a block/unblock.

        Profile views resolve the raw ``user_profiles`` row through a
        per-credential read-through cache (``user_profile_read_api:*``). A block
        or unblock changes who may read each party's profile, so every viewer's
        cached copy of those profiles must go after the relationship write.
        This is defence in depth: the HTTP profile gate also re-evaluates the
        block live on every request, but leaving a warm "allowed" entry behind
        a write is exactly the stale-authorization window we want to close.

        Best-effort: a cache outage must never fail an otherwise committed
        relationship change.
        """
        try:
            from src.core.profiles.manager import invalidate_profile_read_cache

            for user_id in user_ids:
                invalidate_profile_read_cache(int(user_id))
        except Exception as e:
            logger.debug(f"Profile read cache invalidation after block failed: {e}")

    @staticmethod
    def _invalidate_reaction_view_caches() -> None:
        """Drop cached reaction-bearing bodies after a block/unblock.

        Reaction pills (emoji, count and the per-viewer ``me`` flag) are
        resolved through the viewer's TWO-WAY block filter, so a block write
        changes what every viewer may see. Those pills are memoized in the HTTP
        ``reaction_read_api`` body cache AND in the cached channel-message-list
        bodies (``messages_api`` plus the in-process render LRU). A core-only
        invalidation (the reaction manager's block hook) leaves those viewer
        bodies warm: a reader who blocks a reactor keeps seeing that reactor's
        pill and count until the TTL lapses. Drop every reaction-bearing body so
        the next read re-runs the block filter (fail closed).

        Best-effort: a cache outage must never fail an otherwise committed
        relationship change. Lazy import keeps the core tier decoupled from the
        API layer at module load.
        """
        try:
            from src.api.routes.reactions import _invalidate_reaction_read_cache

            _invalidate_reaction_read_cache()
        except Exception as e:
            logger.debug(f"Reaction view cache invalidation after block failed: {e}")

    def get_block(self, block_id: SnowflakeID) -> Optional[BlockedUser]:
        """Get a block record by ID."""
        row = self._db.fetch_one("SELECT * FROM rel_blocked WHERE id = ?", (block_id,))
        if not row:
            return None
        return self._row_to_blocked_user(row)

    def get_blocked_users(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[BlockedUser]:
        """Get list of users blocked by user."""
        # Clamp on BOTH sides: SQLite treats LIMIT -1 as *unlimited*, while an
        # arbitrarily large positive limit still dumps every block in one read.
        limit = self._bounded_list_limit(limit)
        rows = self._db.fetch_all(
            """SELECT * FROM rel_blocked
               WHERE blocker_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [self._row_to_blocked_user(row) for row in rows]

    @cached(ttl=300, prefix="user_blocked")
    def get_blocked_user_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Get list of blocked user IDs."""
        rows = self._db.fetch_all(
            "SELECT blocked_id FROM rel_blocked WHERE blocker_id = ?", (user_id,)
        )
        return [row["blocked_id"] for row in rows]

    @cached(ttl=300, prefix="user_blocked_all")
    def get_all_blocked_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all user IDs that are either blocked by or blocking this user.

        Cached (same TTL as :meth:`get_blocked_user_ids`): the DM event
        fan-out path calls this per participant per event, so an uncached
        query per participant dominated hot-path latency.
        """
        rows = self._db.fetch_all(
            """SELECT blocked_id as user_id FROM rel_blocked WHERE blocker_id = ?
               UNION
               SELECT blocker_id as user_id FROM rel_blocked WHERE blocked_id = ?""",
            (user_id, user_id),
        )
        return [row["user_id"] for row in rows]

    def get_all_blocked_ids_bulk(
        self, user_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Resolve the two-way block set for many users in bounded batches.

        Mirrors :meth:`get_all_blocked_ids` (the UNION of "users I blocked"
        and "users who blocked me") but collapses the N-per-id fan-out - e.g.
        DM event routing over every conversation participant - into a warm
        cache MGET plus at most ``ceil(len(ids) / 500)`` DB round trips.

        Results are identical to calling ``get_all_blocked_ids`` once per id,
        including the fail-closed behaviour for ids with no block rows (an
        empty set, never "unknown"). An id is only ever reported as blocked
        when an authoritative ``rel_blocked`` row exists; a missing/unknown id
        resolves to an empty set, never to "unblocked by fiat". A failed DB
        lookup raises rather than returning a partial answer, so callers can
        fail closed (deny delivery) instead of leaking to a blocked party.
        """
        ids = list(dict.fromkeys(int(uid) for uid in user_ids))
        if not ids:
            return {}

        result: Dict[SnowflakeID, List[SnowflakeID]] = {uid: [] for uid in ids}

        # Warm path: one bulk cache read for every per-user set. A cached empty
        # list is a legitimate hit and is returned as such (not a DB miss).
        keys = {f"{_BLOCKED_IDS_CACHE_PREFIX}:int:{uid}": uid for uid in ids}
        try:
            cached_values = cache_get_many(list(keys))
        except Exception as cache_error:  # a cache outage must never fail open
            logger.debug(f"bulk blocked-id cache read failed: {cache_error}")
            cached_values = {}

        misses: List[int] = []
        for key, uid in keys.items():
            value = cached_values.get(key)
            if value is None:
                misses.append(uid)
                continue
            try:
                result[uid] = [int(item) for item in value]
            except (TypeError, ValueError):
                # Malformed cache entry: re-resolve from the authoritative DB.
                misses.append(uid)

        if not misses:
            return result

        # Cold path: chunk the IN binds. Each execution binds at most
        # ``_BULK_BLOCK_ID_CHUNK_SIZE`` ids to EACH side of the predicate.
        for chunk in _block_id_chunks(misses):
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._db.fetch_all(
                f"""SELECT blocker_id, blocked_id FROM rel_blocked
                    WHERE blocker_id IN ({placeholders})
                       OR blocked_id IN ({placeholders})""",
                (*chunk, *chunk),
            )
            blocked_for: Dict[int, set] = {uid: set() for uid in chunk}
            for row in rows:
                blocker = int(row["blocker_id"])
                blocked = int(row["blocked_id"])
                # A row (B blocks D) contributes D to B's set and B to D's
                # set - exactly the two directions of get_all_blocked_ids.
                if blocker in blocked_for:
                    blocked_for[blocker].add(blocked)
                if blocked in blocked_for:
                    blocked_for[blocked].add(blocker)
            for uid, values in blocked_for.items():
                resolved = list(values)
                result[uid] = resolved
                try:
                    cache_set(
                        f"{_BLOCKED_IDS_CACHE_PREFIX}:int:{uid}",
                        resolved,
                        ttl=_BLOCKED_IDS_CACHE_TTL,
                    )
                except Exception as cache_error:
                    # The authoritative answer is already in ``result``; a
                    # cache write failure must not drop the whole batch.
                    logger.debug(
                        f"bulk blocked-id cache write failed for {uid}: {cache_error}"
                    )

        return result

    def is_blocked(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """Check if blocker has blocked blocked_id."""
        return self._is_blocked(blocker_id, blocked_id)

    def is_blocked_by_either(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if either user has blocked the other, in one bounded lookup.

        The old implementation issued two sequential ``fetch_one`` probes
        (``user_id -> other_id`` then ``other_id -> user_id``), so the common
        "no block" case paid two authoritative DB round trips on every call.
        This gate sits on several hot paths - DM create/send, presence - and is
        also called once per other participant by DM fan-out, so the duplicate
        round trip multiplied across the whole participant set.

        A single OR predicate is exactly equivalent (``EXISTS(A) OR
        EXISTS(B)``) and keeps the same fail-closed contract: the query is
        scoped to only these two user ids - no cross-user rows can satisfy it -
        and a DB error still propagates to the caller instead of resolving to
        "not blocked". The existing indexes on ``rel_blocked.blocker_id`` and
        ``rel_blocked.blocked_id`` serve the two arms.
        """
        row = self._db.fetch_one(
            """SELECT 1 AS ok FROM rel_blocked
               WHERE (blocker_id = ? AND blocked_id = ?)
                  OR (blocker_id = ? AND blocked_id = ?)
               LIMIT 1""",
            (int(user_id), int(other_id), int(other_id), int(user_id)),
        )
        return row is not None
