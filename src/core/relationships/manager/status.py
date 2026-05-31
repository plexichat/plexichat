"""
Relationship status operations mixin for the RelationshipManager.
"""

from typing import Dict, List

from src.core.base import SnowflakeID
from src.core.database import cached

from ..models import (
    Relationship,
    RelationshipStatus,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


class RelationshipStatusMixin(RelationshipHelpersMixin, RelationshipMixinProtocol):
    """Mixin providing relationship status operations."""

    @cached(ttl=60, prefix="all_relationships")
    def get_all_relationships(self, user_id: SnowflakeID) -> Dict[str, List]:
        """Get all relationships (friends, pending, blocked) in fewer database passes."""
        friends = self.get_friends(user_id)

        pending_rows = self._db.fetch_all(
            """SELECT * FROM rel_friend_requests
               WHERE (sender_id = ? OR recipient_id = ?) AND status = 'pending'
               ORDER BY created_at DESC""",
            (user_id, user_id),
        )

        pending_in = []
        pending_out = []
        for row in pending_rows:
            req = self._row_to_friend_request(row)
            if int(row["recipient_id"]) == int(user_id):
                pending_in.append(req)
            else:
                pending_out.append(req)

        blocked = self.get_blocked_users(user_id)

        return {
            "friends": friends,
            "pending_incoming": pending_in,
            "pending_outgoing": pending_out,
            "blocked": blocked,
        }

    def get_relationship(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> Relationship:
        """Get the relationship status between two users."""
        if user_id == target_id:
            return Relationship(
                user_id=user_id,
                target_user_id=target_id,
                status=RelationshipStatus.NONE,
            )

        if self._is_blocked(user_id, target_id):
            block = self._db.fetch_one(
                "SELECT created_at FROM rel_blocked WHERE blocker_id = ? AND blocked_id = ?",
                (user_id, target_id),
            )
            return Relationship(
                user_id=user_id,
                target_user_id=target_id,
                status=RelationshipStatus.BLOCKED,
                created_at=block["created_at"] if block else 0,
            )

        if self._are_friends(user_id, target_id):
            friend = self._db.fetch_one(
                "SELECT created_at FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                (user_id, target_id),
            )
            return Relationship(
                user_id=user_id,
                target_user_id=target_id,
                status=RelationshipStatus.FRIEND,
                created_at=friend["created_at"] if friend else 0,
            )

        outgoing = self._get_pending_request(user_id, target_id)
        if outgoing:
            return Relationship(
                user_id=user_id,
                target_user_id=target_id,
                status=RelationshipStatus.PENDING_OUTGOING,
                created_at=outgoing["created_at"],
            )

        incoming = self._get_pending_request(target_id, user_id)
        if incoming:
            return Relationship(
                user_id=user_id,
                target_user_id=target_id,
                status=RelationshipStatus.PENDING_INCOMING,
                created_at=incoming["created_at"],
            )

        return Relationship(
            user_id=user_id, target_user_id=target_id, status=RelationshipStatus.NONE
        )

    def get_relationship_status(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> RelationshipStatus:
        """Get the relationship status between two users."""
        rel = self.get_relationship(user_id, target_id)
        return rel.status
