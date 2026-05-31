"""
Block operations mixin for the RelationshipManager.
"""

from typing import List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cached

from ..models import BlockedUser
from ..exceptions import (
    AlreadyBlockedError,
    CannotBlockSelfError,
    NotBlockedError,
    UserNotFoundError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


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
        invalidate_friend_ids = getattr(self.get_friend_ids, "invalidate", None)
        if callable(invalidate_friend_ids):
            invalidate_friend_ids(self, blocker_id)
            invalidate_friend_ids(self, blocked_id)
        self._invalidate_all_relationships_cache(blocker_id, blocked_id)

        logger.debug(f"User {blocker_id} blocked user {blocked_id}")
        result = self.get_block(block_id)
        assert result is not None
        return result

    def unblock_user(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """Unblock a user."""
        if not self._is_blocked(blocker_id, blocked_id):
            raise NotBlockedError("User is not blocked")
        self._db.execute(
            "DELETE FROM rel_blocked WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        )
        invalidate_blocked_ids = getattr(self.get_blocked_user_ids, "invalidate", None)
        if callable(invalidate_blocked_ids):
            invalidate_blocked_ids(self, blocker_id)
        self._invalidate_all_relationships_cache(blocker_id, blocked_id)

        logger.debug(f"User {blocker_id} unblocked user {blocked_id}")
        return True

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

    def get_all_blocked_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all user IDs that are either blocked by or blocking this user."""
        rows = self._db.fetch_all(
            """SELECT blocked_id as user_id FROM rel_blocked WHERE blocker_id = ?
               UNION
               SELECT blocker_id as user_id FROM rel_blocked WHERE blocked_id = ?""",
            (user_id, user_id),
        )
        return [row["user_id"] for row in rows]

    def is_blocked(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """Check if blocker has blocked blocked_id."""
        return self._is_blocked(blocker_id, blocked_id)

    def is_blocked_by_either(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if either user has blocked the other."""
        return self._is_blocked(user_id, other_id) or self._is_blocked(
            other_id, user_id
        )
