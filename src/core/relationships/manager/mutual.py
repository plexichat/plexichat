"""
Mutual information operations mixin for the RelationshipManager.
"""

from typing import List

from src.core.base import SnowflakeID

from ..models import MutualInfo
from .helpers import RelationshipHelpersMixin


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
            GROUP BY f2.friend_id
            ORDER BY mutual_count DESC
            LIMIT ?
            """,
            (user_id, user_id, user_id, user_id, user_id, limit),
        )
        return [row["friend_id"] for row in rows]
