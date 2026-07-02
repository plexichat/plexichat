"""
Friends operations mixin for the RelationshipManager.
"""

from typing import List

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cached

from ..models import Friend
from ..exceptions import (
    NotFriendsError,
    SelfRelationshipError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


class FriendsMixin(RelationshipHelpersMixin, RelationshipMixinProtocol):
    """Mixin providing friends operations."""

    def get_friends(self, user_id: SnowflakeID, limit: int = 100) -> List[Friend]:
        """Get list of friends for a user."""
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
