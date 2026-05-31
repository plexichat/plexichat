"""
Friend request operations mixin for the RelationshipManager.
"""

from typing import List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID as SnowflakeIDType

from ..models import (
    FriendRequest,
)
from ..exceptions import (
    AlreadyFriendsError,
    FriendRequestExistsError,
    FriendRequestNotFoundError,
    PermissionDeniedError,
    UserBlockedError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol


class FriendRequestsMixin(RelationshipHelpersMixin, RelationshipMixinProtocol):
    """Mixin providing friend request operations."""

    def send_friend_request(
        self,
        sender_id: SnowflakeIDType,
        recipient_id: SnowflakeIDType,
        message: Optional[str] = None,
    ) -> FriendRequest:
        """Send a friend request to another user."""
        self._validate_users(sender_id, recipient_id)

        if self._is_blocked(sender_id, recipient_id):
            raise UserBlockedError(
                "Cannot send friend request to a user you have blocked",
                blocked_by=sender_id,
                blocked_user=recipient_id,
            )

        if self._is_blocked_by(sender_id, recipient_id):
            raise UserBlockedError(
                "Cannot send friend request - you are blocked by this user",
                blocked_by=recipient_id,
                blocked_user=sender_id,
            )

        if self._are_friends(sender_id, recipient_id):
            raise AlreadyFriendsError("You are already friends with this user")

        existing = self._get_pending_request(sender_id, recipient_id)
        if existing:
            raise FriendRequestExistsError("Friend request already sent")

        reverse = self._get_pending_request(recipient_id, sender_id)
        if reverse:
            return self.accept_friend_request(sender_id, reverse["id"])

        def create_request() -> SnowflakeIDType:
            self._db.execute(
                """DELETE FROM rel_friend_requests
                   WHERE sender_id = ? AND recipient_id = ? AND status != 'pending'""",
                (sender_id, recipient_id),
            )
            now = self._get_timestamp()
            request_id = self._generate_id()
            self._db.execute(
                """INSERT INTO rel_friend_requests
                   (id, sender_id, recipient_id, status, message, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (request_id, sender_id, recipient_id, message, now, now),
            )
            return request_id

        request_id = self._run_in_transaction(create_request)
        self._invalidate_all_relationships_cache(sender_id, recipient_id)

        logger.debug(
            f"Friend request {request_id} sent from {sender_id} to {recipient_id}"
        )
        result = self.get_friend_request(request_id)
        assert result is not None
        return result

    def accept_friend_request(
        self, user_id: SnowflakeIDType, request_id: SnowflakeIDType
    ) -> FriendRequest:
        """Accept a friend request."""
        row = self._db.fetch_one(
            "SELECT * FROM rel_friend_requests WHERE id = ? AND status = 'pending'",
            (request_id,),
        )
        if not row:
            raise FriendRequestNotFoundError(
                "Friend request not found or already processed"
            )
        if row["recipient_id"] != user_id:
            raise PermissionDeniedError(
                "Cannot accept a friend request not sent to you"
            )

        def accept_request() -> None:
            now = self._get_timestamp()
            self._db.execute(
                "UPDATE rel_friend_requests SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now, request_id),
            )
            friend_id_1 = self._generate_id()
            friend_id_2 = self._generate_id()
            self._db.insert_or_ignore(
                "rel_friends",
                ["id", "user_id", "friend_id", "created_at"],
                (friend_id_1, row["sender_id"], row["recipient_id"], now),
            )
            self._db.insert_or_ignore(
                "rel_friends",
                ["id", "user_id", "friend_id", "created_at"],
                (friend_id_2, row["recipient_id"], row["sender_id"], now),
            )

        self._run_in_transaction(accept_request)
        invalidate_friend_ids = getattr(self.get_friend_ids, "invalidate", None)
        if callable(invalidate_friend_ids):
            invalidate_friend_ids(self, row["sender_id"])
            invalidate_friend_ids(self, row["recipient_id"])
        self._invalidate_all_relationships_cache(row["sender_id"], row["recipient_id"])

        logger.debug(f"Friend request {request_id} accepted")
        result = self.get_friend_request(request_id)
        assert result is not None
        return result

    def decline_friend_request(
        self, user_id: SnowflakeIDType, request_id: SnowflakeIDType
    ) -> FriendRequest:
        """Decline a friend request."""
        row = self._db.fetch_one(
            "SELECT * FROM rel_friend_requests WHERE id = ? AND status = 'pending'",
            (request_id,),
        )
        if not row:
            raise FriendRequestNotFoundError(
                "Friend request not found or already processed"
            )
        if row["recipient_id"] != user_id:
            raise PermissionDeniedError(
                "Cannot decline a friend request not sent to you"
            )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE rel_friend_requests SET status = 'declined', updated_at = ? WHERE id = ?",
            (now, request_id),
        )
        self._invalidate_all_relationships_cache(row["sender_id"], row["recipient_id"])

        logger.debug(f"Friend request {request_id} declined")
        result = self.get_friend_request(request_id)
        assert result is not None
        return result

    def cancel_friend_request(
        self, user_id: SnowflakeIDType, request_id: SnowflakeIDType
    ) -> FriendRequest:
        """Cancel a sent friend request."""
        row = self._db.fetch_one(
            "SELECT * FROM rel_friend_requests WHERE id = ? AND status = 'pending'",
            (request_id,),
        )
        if not row:
            raise FriendRequestNotFoundError(
                "Friend request not found or already processed"
            )
        if row["sender_id"] != user_id:
            raise PermissionDeniedError(
                "Cannot cancel a friend request you did not send"
            )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE rel_friend_requests SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, request_id),
        )
        self._invalidate_all_relationships_cache(row["sender_id"], row["recipient_id"])

        logger.debug(f"Friend request {request_id} cancelled")
        result = self.get_friend_request(request_id)
        assert result is not None
        return result

    def get_friend_request(
        self, request_id: SnowflakeIDType
    ) -> Optional[FriendRequest]:
        """Get a friend request by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM rel_friend_requests WHERE id = ?", (request_id,)
        )
        if not row:
            return None
        return self._row_to_friend_request(row)

    def get_pending_requests_incoming(
        self, user_id: SnowflakeIDType, limit: int = 100
    ) -> List[FriendRequest]:
        """Get incoming pending friend requests."""
        rows = self._db.fetch_all(
            """SELECT * FROM rel_friend_requests
               WHERE recipient_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [self._row_to_friend_request(row) for row in rows]

    def get_pending_requests_outgoing(
        self, user_id: SnowflakeIDType, limit: int = 100
    ) -> List[FriendRequest]:
        """Get outgoing pending friend requests."""
        rows = self._db.fetch_all(
            """SELECT * FROM rel_friend_requests
               WHERE sender_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [self._row_to_friend_request(row) for row in rows]

    def get_incoming_requests(
        self, user_id: SnowflakeIDType, limit: int = 100
    ) -> List[FriendRequest]:
        """Alias for compatibility with legacy tests."""
        return self.get_pending_requests_incoming(user_id, limit)

    def get_outgoing_requests(
        self, user_id: SnowflakeIDType, limit: int = 100
    ) -> List[FriendRequest]:
        """Alias for compatibility with legacy tests."""
        return self.get_pending_requests_outgoing(user_id, limit)
