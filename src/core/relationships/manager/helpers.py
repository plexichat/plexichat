"""
Shared helpers mixin for the RelationshipManager.
Provides validation, database helpers, and row converters.
"""

from typing import Any, Callable, Dict, Optional, TypeVar

import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

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


class RelationshipHelpersMixin(BaseManager):
    """Mixin providing shared helper methods for relationship operations."""

    def __init__(
        self, db: Any, auth_module: Any = None, servers_module: Any = None
    ) -> None:
        super().__init__(db, auth_module)
        self._servers = servers_module
        logger.info("Relationship module initialized")

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
        """Check if two users are friends."""
        row = self._db.fetch_one(
            "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (user_id, other_id),
        )
        return row is not None

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
        """Invalidate cached aggregate relationship state for the given users."""
        from src.core.database import invalidate_pattern

        seen_ids = set()
        for user_id in user_ids:
            normalized_id = int(user_id)
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            invalidate_pattern(f"all_relationships:*{normalized_id}*")

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
