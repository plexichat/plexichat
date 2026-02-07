"""
Relationship manager - Core business logic for relationship operations.

Handles friend requests, blocking, and relationship queries with proper
validation and database interactions.
"""

from typing import Optional, List, Dict

import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from .models import (
    Relationship,
    FriendRequest,
    BlockedUser,
    Friend,
    MutualInfo,
    RelationshipStatus,
    FriendRequestStatus,
)
from .exceptions import (
    UserNotFoundError,
    SelfRelationshipError,
    FriendRequestNotFoundError,
    FriendRequestExistsError,
    AlreadyFriendsError,
    NotFriendsError,
    UserBlockedError,
    AlreadyBlockedError,
    NotBlockedError,
    CannotBlockSelfError,
    PermissionDeniedError,
)
from .schema import create_tables


from src.core.database import (
    cached,
)


class RelationshipManager(BaseManager):
    """Core relationship manager handling all operations."""

    def __init__(self, db, auth_module=None, servers_module=None):
        """
        Initialize the relationship manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            servers_module: Optional servers module for mutual servers
        """
        super().__init__(db, auth_module)
        self._servers = servers_module

        create_tables(db)

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
        blocked_ids = self.get_blocked_user_ids(blocker_id)
        return int(blocked_id) in [int(bid) for rid in blocked_ids for bid in ([rid] if not isinstance(rid, list) else rid)] if blocked_ids else False

    def _is_blocked_by(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if user is blocked by other."""
        return self._is_blocked(other_id, user_id)

    def _are_friends(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool:
        """Check if two users are friends."""
        friend_ids = self.get_friend_ids(user_id)
        return int(other_id) in [int(fid) for rid in friend_ids for fid in ([rid] if not isinstance(rid, list) else rid)] if friend_ids else False

    def _get_pending_request(
        self, sender_id: SnowflakeID, recipient_id: SnowflakeID
    ) -> Optional[Dict]:
        """Get pending friend request between users."""
        return self._db.fetch_one(
            """SELECT * FROM rel_friend_requests 
               WHERE sender_id = ? AND recipient_id = ? AND status = 'pending'""",
            (sender_id, recipient_id),
        )

    # === Friend Request Operations ===

    def send_friend_request(
        self,
        sender_id: SnowflakeID,
        recipient_id: SnowflakeID,
        message: Optional[str] = None,
    ) -> FriendRequest:
        """
        Send a friend request to another user.

        Args:
            sender_id: ID of user sending the request
            recipient_id: ID of user receiving the request
            message: Optional message with the request

        Returns:
            Created FriendRequest

        Raises:
            SelfRelationshipError: Cannot send request to self
            UserNotFoundError: User does not exist
            UserBlockedError: One user has blocked the other
            AlreadyFriendsError: Users are already friends
            FriendRequestExistsError: Pending request already exists
        """
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

        # Delete any old non-pending requests to allow resending
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

        logger.debug(
            f"Friend request {request_id} sent from {sender_id} to {recipient_id}"
        )

        result = self.get_friend_request(request_id)
        assert result is not None  # Should exist since we just created it
        return result

    def accept_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest:
        """
        Accept a friend request.

        Args:
            user_id: ID of user accepting (must be recipient)
            request_id: ID of the friend request

        Returns:
            Updated FriendRequest

        Raises:
            FriendRequestNotFoundError: Request not found or not pending
        """
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

        # Invalidate friends cache for both users
        from src.core.database import invalidate_cached
        invalidate_cached(self.get_friend_ids, row["sender_id"])
        invalidate_cached(self.get_friend_ids, row["recipient_id"])

        logger.debug(f"Friend request {request_id} accepted")

        result = self.get_friend_request(request_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def decline_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest:
        """
        Decline a friend request.

        Args:
            user_id: ID of user declining (must be recipient)
            request_id: ID of the friend request

        Returns:
            Updated FriendRequest
        """
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

        logger.debug(f"Friend request {request_id} declined")

        result = self.get_friend_request(request_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def cancel_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest:
        """
        Cancel a sent friend request.

        Args:
            user_id: ID of user cancelling (must be sender)
            request_id: ID of the friend request

        Returns:
            Updated FriendRequest
        """
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

        logger.debug(f"Friend request {request_id} cancelled")

        result = self.get_friend_request(request_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def get_friend_request(self, request_id: SnowflakeID) -> Optional[FriendRequest]:
        """Get a friend request by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM rel_friend_requests WHERE id = ?", (request_id,)
        )

        if not row:
            return None

        return self._row_to_friend_request(row)

    def get_pending_requests_incoming(
        self, user_id: SnowflakeID, limit: int = 100
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
        self, user_id: SnowflakeID, limit: int = 100
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
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]:
        """Alias for compatibility with legacy tests."""
        return self.get_pending_requests_incoming(user_id, limit)

    def get_outgoing_requests(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]:
        """Alias for compatibility with legacy tests."""
        return self.get_pending_requests_outgoing(user_id, limit)

    # === Friends Operations ===

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
        """
        Remove a friend (unfriend).

        Args:
            user_id: ID of user removing friend
            friend_id: ID of friend to remove

        Returns:
        """
        if user_id == friend_id:
            raise SelfRelationshipError("Cannot unfriend yourself")

        if not self._are_friends(user_id, friend_id):
            raise NotFriendsError("You are not friends with this user")

        self._db.execute(
            "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (user_id, friend_id),
        )
        self._db.execute(
            "DELETE FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (friend_id, user_id),
        )

        # Invalidate friends cache for both users
        from src.core.database import invalidate_cached
        invalidate_cached(self.get_friend_ids, user_id)
        invalidate_cached(self.get_friend_ids, friend_id)

        logger.debug(f"Friendship removed between {user_id} and {friend_id}")

        return True

    # === Block Operations ===

    def block_user(
        self,
        blocker_id: SnowflakeID,
        blocked_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> BlockedUser:
        """
        Block a user.

        Args:
            blocker_id: ID of user doing the blocking
            blocked_id: ID of user being blocked
            reason: Optional reason for blocking

        Returns:
            Created BlockedUser

        Raises:
            CannotBlockSelfError: Cannot block yourself
            AlreadyBlockedError: User already blocked
        """
        if blocker_id == blocked_id:
            raise CannotBlockSelfError("Cannot block yourself")

        if not self._user_exists(blocked_id):
            raise UserNotFoundError(f"User {blocked_id} not found")

        if self._is_blocked(blocker_id, blocked_id):
            raise AlreadyBlockedError("User is already blocked")

        now = self._get_timestamp()
        block_id = self._generate_id()

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

        # Invalidate caches
        from src.core.database import invalidate_cached
        invalidate_cached(self.get_blocked_user_ids, blocker_id)
        # Also invalidate friends in case they were friends
        invalidate_cached(self.get_friend_ids, blocker_id)
        invalidate_cached(self.get_friend_ids, blocked_id)

        logger.debug(f"User {blocker_id} blocked user {blocked_id}")

        result = self.get_block(block_id)
        assert result is not None  # Should exist since we just created it
        return result

    def unblock_user(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool:
        """
        Unblock a user.

        Args:
            blocker_id: ID of user doing the unblocking
            blocked_id: ID of user being unblocked

        Returns:
            True if unblocked

        Raises:
            NotBlockedError: User is not blocked
        """
        if not self._is_blocked(blocker_id, blocked_id):
            raise NotBlockedError("User is not blocked")

        self._db.execute(
            "DELETE FROM rel_blocked WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        )

        # Invalidate cache
        self.get_blocked_user_ids.invalidate(blocker_id)

        logger.debug(f"User {blocker_id} unblocked user {blocked_id}")

        return True

    def get_all_relationships(self, user_id: SnowflakeID) -> Dict[str, List]:
        """
        Get all relationships (friends, pending, blocked) in fewer database passes.
        
        Returns:
            Dict containing lists of Friend, FriendRequest, and BlockedUser objects.
        """
        # 1. Fetch friends
        friends = self.get_friends(user_id)
        
        # 2. Fetch all pending requests (incoming and outgoing) in one query
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
                
        # 3. Fetch blocked users
        blocked = self.get_blocked_users(user_id)
        
        return {
            "friends": friends,
            "pending_incoming": pending_in,
            "pending_outgoing": pending_out,
            "blocked": blocked
        }

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
        """Get list of all user IDs that are either blocked by or blocking this user."""
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

    # === Relationship Status ===

    def get_relationship(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> Relationship:
        """
        Get the relationship status between two users.

        Args:
            user_id: ID of the user checking
            target_id: ID of the target user

        Returns:
            Relationship with status
        """
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

    # === Mutual Information ===

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

    # === Helper Methods ===

    def _row_to_friend_request(self, row) -> FriendRequest:
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

    def _row_to_friend(self, row) -> Friend:
        """Convert database row to Friend."""
        return Friend(
            id=row["id"],
            user_id=row["user_id"],
            friend_id=row["friend_id"],
            created_at=row["created_at"],
        )

    def _row_to_blocked_user(self, row) -> BlockedUser:
        """Convert database row to BlockedUser."""
        return BlockedUser(
            id=row["id"],
            blocker_id=row["blocker_id"],
            blocked_id=row["blocked_id"],
            reason=row["reason"] if row["reason"] else None,
            created_at=row["created_at"],
        )

    def get_relationship_status(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> RelationshipStatus:
        """Get the relationship status between two users."""
        rel = self.get_relationship(user_id, target_id)
        return rel.status

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
