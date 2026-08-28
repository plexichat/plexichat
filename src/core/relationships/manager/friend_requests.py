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
    RelationshipValidationError,
    UserBlockedError,
)
from .helpers import RelationshipHelpersMixin
from .protocol import RelationshipMixinProtocol

MAX_FRIEND_REQUEST_MESSAGE_LENGTH = 256

# Minimum delay before the SAME sender may re-send to the SAME recipient
# after a previous request was declined/cancelled (anti-harassment cooldown).
FRIEND_REQUEST_RESEND_COOLDOWN_SECONDS = 60


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

        if message is not None and len(message) > MAX_FRIEND_REQUEST_MESSAGE_LENGTH:
            raise RelationshipValidationError(
                f"Friend request message exceeds maximum length of "
                f"{MAX_FRIEND_REQUEST_MESSAGE_LENGTH} characters"
            )

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
            accepted = self.accept_friend_request(sender_id, reverse["id"])
            # L4: accept_friend_request invalidates both users' caches today,
            # but the outer contract is that THIS method invalidates on every
            # successful send. Do not depend on accept's internals - invalidate
            # explicitly so the guarantee holds even if accept's invalidation
            # ever changes.
            self._invalidate_all_relationships_cache(sender_id, recipient_id)
            return accepted

        def create_request() -> SnowflakeIDType:
            now = self._get_timestamp()

            # Cooldown: block instant re-request after a decline/cancel so a
            # declined user cannot retry indefinitely without waiting.
            recent = self._db.fetch_one(
                """SELECT updated_at FROM rel_friend_requests
                   WHERE sender_id = ? AND recipient_id = ?
                   AND status IN ('declined', 'cancelled')
                   ORDER BY updated_at DESC LIMIT 1""",
                (sender_id, recipient_id),
            )
            if recent is not None:
                last_updated = int(recent["updated_at"] or 0)
                cooldown_ms = FRIEND_REQUEST_RESEND_COOLDOWN_SECONDS * 1000
                if now - last_updated < cooldown_ms:
                    raise RelationshipValidationError(
                        "Please wait before sending another friend request to this user"
                    )

            self._db.execute(
                """DELETE FROM rel_friend_requests
                   WHERE sender_id = ? AND recipient_id = ? AND status != 'pending'""",
                (sender_id, recipient_id),
            )
            request_id = self._generate_id()
            self._db.execute(
                """INSERT INTO rel_friend_requests
                   (id, sender_id, recipient_id, status, message, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (request_id, sender_id, recipient_id, message, now, now),
            )
            return request_id

        try:
            request_id = self._run_in_transaction(create_request)
        except Exception as e:
            error_str = str(e).lower()
            if (
                "unique" in error_str
                or "constraint" in error_str
                or "duplicate" in error_str
            ):
                logger.debug(
                    f"UNIQUE constraint violation during friend request from {sender_id} to {recipient_id}, "
                    f"checking for reverse request (race condition)"
                )
                reverse = self._get_pending_request(recipient_id, sender_id)
                if reverse:
                    accepted = self.accept_friend_request(sender_id, reverse["id"])
                    # L4: same explicit invalidation as the pre-check path.
                    self._invalidate_all_relationships_cache(sender_id, recipient_id)
                    return accepted
                raise FriendRequestExistsError("Friend request already exists")
            raise

        self._invalidate_all_relationships_cache(sender_id, recipient_id)
        logger.debug(
            f"Friend request {request_id} sent from {sender_id} to {recipient_id}"
        )
        result = self.get_friend_request(request_id)
        if result is None:
            raise FriendRequestNotFoundError("Friend request not found after creation")
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

            # Re-validate blocks INSIDE the transaction: a concurrent block
            # (which declines pending requests both ways) must never be
            # overridden into an active friendship by an in-flight accept.
            if self._is_blocked(
                row["sender_id"], row["recipient_id"]
            ) or self._is_blocked(row["recipient_id"], row["sender_id"]):
                raise UserBlockedError(
                    "Cannot accept a friend request involving a block",
                    blocked_by=row["sender_id"],
                    blocked_user=row["recipient_id"],
                )

            # Conditional update guarded on status='pending' so a concurrent
            # decline/cancel wins and the accept becomes a no-op.
            cursor = self._db.execute(
                """UPDATE rel_friend_requests SET status = 'accepted', updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (now, request_id),
            )
            if cursor.rowcount != 1:
                raise FriendRequestNotFoundError(
                    "Friend request not found or already processed"
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
        if result is None:
            raise FriendRequestNotFoundError(
                "Friend request not found after acceptance"
            )
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
        cursor = self._db.execute(
            """UPDATE rel_friend_requests SET status = 'declined', updated_at = ?
               WHERE id = ? AND status = 'pending'""",
            (now, request_id),
        )
        if cursor.rowcount != 1:
            raise FriendRequestNotFoundError(
                "Friend request not found or already processed"
            )
        self._invalidate_all_relationships_cache(row["sender_id"], row["recipient_id"])

        logger.debug(f"Friend request {request_id} declined")
        result = self.get_friend_request(request_id)
        if result is None:
            raise FriendRequestNotFoundError("Friend request not found after decline")
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
        cursor = self._db.execute(
            """UPDATE rel_friend_requests SET status = 'cancelled', updated_at = ?
               WHERE id = ? AND status = 'pending'""",
            (now, request_id),
        )
        if cursor.rowcount != 1:
            raise FriendRequestNotFoundError(
                "Friend request not found or already processed"
            )
        self._invalidate_all_relationships_cache(row["sender_id"], row["recipient_id"])

        logger.debug(f"Friend request {request_id} cancelled")
        result = self.get_friend_request(request_id)
        if result is None:
            raise FriendRequestNotFoundError(
                "Friend request not found after cancellation"
            )
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
