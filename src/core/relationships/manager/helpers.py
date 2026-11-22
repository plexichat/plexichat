"""
Shared helpers mixin for the RelationshipManager.
Provides validation, database helpers, and row converters.
"""

from typing import Any, Callable, Dict, Optional, TypeVar

import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from src.core.bounded_lru import BoundedLRU

from ..models import (
    BlockedUser,
    Friend,
    FriendRequest,
    FriendRequestStatus,
)
from ..exceptions import (
    SelfRelationshipError,
    UserNotFoundError,
)

_TransactionResult = TypeVar("_TransactionResult")

# Sentinel so a cached ``False`` (not friends) is distinguishable from a miss.
_FRIEND_CHECK_MISS = object()

# Bounds for the per-pair friendship-check cache. ``_are_friends`` is a
# primitive consulted from several relationship paths; within one request the
# same (viewer, other) pair is frequently probed more than once (e.g. a block
# guard followed by a status read). Capping entries, bytes and TTL keeps that
# dedup process-local and incapable of unbounded growth from request-derived
# ids. TTL is only a safety net: every mutation invalidates eagerly.
_FRIEND_CHECK_MAX_ENTRIES = 2048
_FRIEND_CHECK_MAX_BYTES = 256 * 1024
_FRIEND_CHECK_TTL_SECONDS = 30.0

# Hard ceiling for every relationship *list* read (friends / pending requests
# / blocked users). The call sites used to do ``limit = max(0, limit)``: that
# stops SQLite's ``LIMIT -1`` meaning "unlimited", but an arbitrarily large
# positive value still dumped the caller's whole graph in one query -- including
# the private 256-character friend-request messages. Internal callers and
# plugins can pass a limit directly, so cap it at the shared choke point too
# instead of relying on each HTTP route to remember to bound its query param.
MAX_RELATIONSHIP_LIST_LIMIT = 500


class RelationshipHelpersMixin(BaseManager):
    """Mixin providing shared helper methods for relationship operations."""

    def __init__(
        self, db: Any, auth_module: Any = None, servers_module: Any = None
    ) -> None:
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._friend_check_cache = BoundedLRU(
            max_entries=_FRIEND_CHECK_MAX_ENTRIES,
            max_bytes=_FRIEND_CHECK_MAX_BYTES,
            ttl_seconds=_FRIEND_CHECK_TTL_SECONDS,
        )
        logger.info("Relationship module initialized")

    @staticmethod
    def _bounded_list_limit(limit, default: int = 100) -> int:
        """Return a finite page size in ``[0, MAX_RELATIONSHIP_LIST_LIMIT]``.

        ``max(0, limit)`` alone was not a bound: a huge positive integer (or a
        non-integer that happens to compare) reached ``LIMIT ?`` unchanged.
        Non-numeric values fall back to the caller's documented default rather
        than raising, preserving the previous tolerant behaviour at the list
        boundary while keeping the read bounded.
        """
        try:
            value = int(limit)
        except (TypeError, ValueError, OverflowError):
            value = default
        return max(0, min(value, MAX_RELATIONSHIP_LIST_LIMIT))

    def _validate_users(self, user_id: SnowflakeID, target_id: SnowflakeID) -> None:
        """Validate both users exist and are different."""
        if user_id == target_id:
            raise SelfRelationshipError("Cannot create relationship with yourself")
        if not self._user_exists(user_id):
            raise UserNotFoundError(f"User {user_id} not found")
        if not self._user_exists(target_id):
            raise UserNotFoundError(f"User {target_id} not found")

    def _is_blocked(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """Check if blocker has blocked blocked_id."""
        row = self._db.fetch_one(
            "SELECT 1 as ok FROM rel_blocked WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        )
        return row is not None

    def _is_blocked_by(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if user is blocked by other."""
        return self._is_blocked(other_id, user_id)

    def _are_friends(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if two users are friends.

        Cached per (user, other) pair so repeated checks within a request only
        hit the database once. Both outcomes are cached: a negative answer is
        just as authoritative as a positive one. The cache is cleared by
        ``_invalidate_all_relationships_cache`` on every friendship/block
        mutation, so a stale verdict cannot outlive an add or remove.
        """
        key = (int(user_id), int(other_id))
        cached = self._friend_check_cache.get(key, _FRIEND_CHECK_MISS)
        if cached is not _FRIEND_CHECK_MISS:
            return bool(cached)

        row = self._db.fetch_one(
            "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (user_id, other_id),
        )
        result = row is not None
        self._friend_check_cache.set(key, result)
        return result

    def _get_pending_request(
        self, sender_id: SnowflakeID, recipient_id: SnowflakeID
    ) -> Optional[Dict]:
        """Get pending friend request between users."""
        return self._db.fetch_one(
            """SELECT * FROM rel_friend_requests
               WHERE sender_id = ? AND recipient_id = ? AND status = 'pending'""",
            (sender_id, recipient_id),
        )

    def _run_in_transaction(
        self, operation: Callable[[], _TransactionResult]
    ) -> _TransactionResult:
        """Execute a relationship mutation inside an explicit DB transaction."""
        self._db.begin_transaction()
        try:
            result = operation()
        except Exception:
            self._db.rollback()
            raise
        self._db.commit()
        return result

    def _invalidate_all_relationships_cache(self, *user_ids: SnowflakeID) -> None:
        """Invalidate cached aggregate relationship state for the given users.

        Uses a targeted delete instead of ``invalidate_pattern``. The latter
        issues a Valkey ``KEYS`` scan over the *entire* keyspace for every
        mutation (send/accept/block/unblock/delete), which dominated hot-path
        latency once the cache held a meaningful number of keys. The cache key
        is deterministic — ``get_all_relationships`` is a method, so the
        decorator drops ``self`` and tags the remaining int argument as
        ``all_relationships:int:<uid>`` (see ``generate_cache_key``) — so an
        exact delete both avoids the scan and is strictly more precise than
        the previous ``all_relationships:*:<uid>`` glob.
        """
        from src.core.database import invalidate_cached

        # Presence fan-out targets are a SECURITY verdict: who receives a
        # user's PRESENCE_UPDATE frames (real status + custom status/emoji).
        # ``_get_presence_targets(uid)`` memoizes that audience under the
        # deterministic key ``presence_targets:int:<uid>`` for a short TTL. A
        # block/friend change alters the audience for *both* parties (the
        # block lookup is two-way), and every mutation funnels through this
        # hook with the affected ids. Without an eager evict, a just-blocked
        # user keeps receiving the blocker's real presence until the TTL lapses
        # -- the delivery-side twin of the already-handled ``presence_read_api``
        # cache. Drop the exact keys (no KEYS scan) for each mutated id.
        # Friend-check verdicts are pair-scoped but this hook receives an
        # arbitrary set of mutated user ids (send/accept/remove/block), not a
        # reliable pair, so drop the whole small bounded cache. A miss simply
        # recomputes from the authoritative DB. ``getattr`` keeps this safe for
        # the unbound call used in the aggregate-invalidation regression test.
        cache = getattr(self, "_friend_check_cache", None)
        if cache is not None:
            cache.clear()

        # The gateway audience-resolution caches (EventRouter + gateway_emit)
        # are separate L0 layers from the DB-tier ``presence_targets:*`` keys
        # evicted below. They memoize fully resolved, block-filtered audiences:
        #
        # * EventRouter._presence_audience_cache -> PRESENCE_UPDATE recipients;
        # * EventRouter._dm_participant_cache     -> DM message event recipients;
        # * gateway_emit._channel_recipient_cache -> typing / read-receipt /
        #   reaction / poll / bulk-delete recipients.
        #
        # Each is keyed by scope + actor + source identity (+ RBAC epoch), NOT
        # by a block version, so a block/unblock or friend change leaves the
        # warm audience stale for the whole 1s TTL. The cached list is already
        # the product of the two-way block filter: replaying it after a block
        # keeps delivering the actor's content to the just-blocked user (and,
        # on unblock, keeps withholding it). Evict all three here with the
        # other relationship-derived caches. Lazy import keeps the relationship
        # tier decoupled from the gateway at module load (writes are rare).
        # The single-line router import is kept verbatim so the committed-tree
        # guard's 70cd4627 presence-eviction pin still matches.
        from src.core.events.router import clear_presence_audience_cache, clear_dm_participant_cache
        from src.core.events.gateway_emit import clear_channel_recipient_cache

        clear_presence_audience_cache()
        clear_dm_participant_cache()
        clear_channel_recipient_cache()

        seen_ids = set()
        for user_id in user_ids:
            normalized_id = int(user_id)
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            invalidate_cached(f"all_relationships:int:{normalized_id}")
            invalidate_cached(f"presence_targets:int:{normalized_id}")

    # === Row converters ===

    def _row_to_friend_request(self, row: Any) -> FriendRequest:
        """Convert database row to FriendRequest."""
        return FriendRequest(
            id=row["id"],
            sender_id=row["sender_id"],
            recipient_id=row["recipient_id"],
            status=FriendRequestStatus(row["status"]),
            message=row["message"] if row["message"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_friend(self, row: Any) -> Friend:
        """Convert database row to Friend."""
        return Friend(
            id=row["id"],
            user_id=row["user_id"],
            friend_id=row["friend_id"],
            created_at=row["created_at"],
        )

    def _row_to_blocked_user(self, row: Any) -> BlockedUser:
        """Convert database row to BlockedUser."""
        return BlockedUser(
            id=row["id"],
            blocker_id=row["blocker_id"],
            blocked_id=row["blocked_id"],
            reason=row["reason"] if row["reason"] else None,
            created_at=row["created_at"],
        )
