"""
Relationships collector for rel_friends, rel_friend_requests, rel_blocked tables.

Collects friends list, incoming/outgoing friend requests, and blocked users.
Includes joined usernames for readability.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class RelationshipsCollector(BaseCollector):
    """Collects relationship data from rel_friends, rel_friend_requests, rel_blocked tables."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect relationship data."""
        return {
            "friends": self._collect_friends(user_id),
            "incoming_friend_requests": self._collect_incoming_requests(user_id),
            "outgoing_friend_requests": self._collect_outgoing_requests(user_id),
            "blocked_users": self._collect_blocked(user_id),
        }

    def _collect_friends(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect rel_friends with friend username."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT rf.*, au.username as friend_username
                FROM rel_friends rf
                JOIN auth_users au ON rf.friend_id = au.id
                WHERE rf.user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect friends for user {user_id}: {e}")
            return []

    def _collect_incoming_requests(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect incoming friend requests with sender username."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT rfr.*, au.username as sender_username
                FROM rel_friend_requests rfr
                JOIN auth_users au ON rfr.sender_id = au.id
                WHERE rfr.recipient_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect incoming requests for user {user_id}: {e}")
            return []

    def _collect_outgoing_requests(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect outgoing friend requests with recipient username."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT rfr.*, au.username as recipient_username
                FROM rel_friend_requests rfr
                JOIN auth_users au ON rfr.recipient_id = au.id
                WHERE rfr.sender_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect outgoing requests for user {user_id}: {e}")
            return []

    def _collect_blocked(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect rel_blocked with blocked username."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT rb.*, au.username as blocked_username
                FROM rel_blocked rb
                JOIN auth_users au ON rb.blocked_id = au.id
                WHERE rb.blocker_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect blocked for user {user_id}: {e}")
            return []
