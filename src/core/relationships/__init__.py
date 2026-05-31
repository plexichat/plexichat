"""
Relationships module - Zero-friction API for friend and block management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import relationships
    relationships.setup(db, auth, servers)

    # In any other file (use directly)
    from src.core import relationships
    request = relationships.send_friend_request(user_id=1, recipient_id=2)
"""

from typing import Any, Optional, List, Dict

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
    RelationshipError,
    UserNotFoundError,
    SelfRelationshipError,
    FriendRequestNotFoundError,
    FriendRequestExistsError,
    AlreadyFriendsError,
    NotFriendsError,
    PermissionDeniedError,
    UserBlockedError,
    AlreadyBlockedError,
    NotBlockedError,
    CannotBlockSelfError,
)

__all__ = [
    # Models
    "Relationship",
    "FriendRequest",
    "BlockedUser",
    "Friend",
    "MutualInfo",
    "RelationshipStatus",
    "FriendRequestStatus",
    # Exceptions
    "RelationshipError",
    "UserNotFoundError",
    "SelfRelationshipError",
    "FriendRequestNotFoundError",
    "FriendRequestExistsError",
    "AlreadyFriendsError",
    "NotFriendsError",
    "PermissionDeniedError",
    "UserBlockedError",
    "AlreadyBlockedError",
    "NotBlockedError",
    "CannotBlockSelfError",
    # Setup
    "setup",
    # Friend request operations
    "send_friend_request",
    "accept_friend_request",
    "decline_friend_request",
    "cancel_friend_request",
    "get_friend_request",
    "get_pending_requests_incoming",
    "get_pending_requests_outgoing",
    # Friends operations
    "get_friends",
    "get_friend_ids",
    "remove_friend",
    # Block operations
    "block_user",
    "unblock_user",
    "get_blocked_users",
    "get_blocked_user_ids",
    "is_blocked",
    "is_blocked_by_either",
    # Relationship status
    "get_relationship",
    "get_all_relationships",
    # Mutual information
    "get_mutual_friends",
    "get_mutual_friend_count",
    "get_mutual_servers",
    "get_mutual_server_count",
    "get_mutual_info",
]

_manager = None
_setup_complete = False


def setup(
    db: Any, auth_module: Optional[Any] = None, servers_module: Optional[Any] = None
) -> None:
    """
    Initialize the relationships module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for user verification
        servers_module: Optional servers module for mutual servers
    """
    global _manager, _setup_complete

    from .manager.composer import RelationshipManager

    _manager = RelationshipManager(db, auth_module, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Relationships module not initialized. Call relationships.setup(db) first."
        )
    return _manager


# === Friend Request Operations ===


def send_friend_request(
    sender_id: int, recipient_id: int, message: Optional[str] = None
) -> FriendRequest:
    """Send a friend request to another user."""
    return _get_manager().send_friend_request(sender_id, recipient_id, message)


def accept_friend_request(user_id: int, request_id: int) -> FriendRequest:
    """Accept a friend request."""
    return _get_manager().accept_friend_request(user_id, request_id)


def decline_friend_request(user_id: int, request_id: int) -> FriendRequest:
    """Decline a friend request."""
    return _get_manager().decline_friend_request(user_id, request_id)


def cancel_friend_request(user_id: int, request_id: int) -> FriendRequest:
    """Cancel a sent friend request."""
    return _get_manager().cancel_friend_request(user_id, request_id)


def get_friend_request(request_id: int) -> Optional[FriendRequest]:
    """Get a friend request by ID."""
    return _get_manager().get_friend_request(request_id)


def get_pending_requests_incoming(
    user_id: int, limit: int = 100
) -> List[FriendRequest]:
    """Get incoming pending friend requests."""
    return _get_manager().get_pending_requests_incoming(user_id, limit)


def get_pending_requests_outgoing(
    user_id: int, limit: int = 100
) -> List[FriendRequest]:
    """Get outgoing pending friend requests."""
    return _get_manager().get_pending_requests_outgoing(user_id, limit)


# === Friends Operations ===


def get_friends(user_id: int, limit: int = 100) -> List[Friend]:
    """Get list of friends for a user."""
    return _get_manager().get_friends(user_id, limit)


def get_friend_ids(user_id: int) -> List[int]:
    """Get list of friend user IDs."""
    return _get_manager().get_friend_ids(user_id)


def remove_friend(user_id: int, friend_id: int) -> bool:
    """Remove a friend (unfriend)."""
    return _get_manager().remove_friend(user_id, friend_id)


# === Block Operations ===


def block_user(
    blocker_id: int, blocked_id: int, reason: Optional[str] = None
) -> BlockedUser:
    """Block a user."""
    return _get_manager().block_user(blocker_id, blocked_id, reason)


def unblock_user(blocker_id: int, blocked_id: int) -> bool:
    """Unblock a user."""
    return _get_manager().unblock_user(blocker_id, blocked_id)


def get_blocked_users(user_id: int, limit: int = 100) -> List[BlockedUser]:
    """Get list of users blocked by user."""
    return _get_manager().get_blocked_users(user_id, limit)


def get_blocked_user_ids(user_id: int) -> List[int]:
    """Get list of blocked user IDs."""
    return _get_manager().get_blocked_user_ids(user_id)


def is_blocked(blocker_id: int, blocked_id: int) -> bool:
    """Check if blocker has blocked blocked_id."""
    return _get_manager().is_blocked(blocker_id, blocked_id)


def is_blocked_by_either(user_id: int, other_id: int) -> bool:
    """Check if either user has blocked the other."""
    return _get_manager().is_blocked_by_either(user_id, other_id)


# === Relationship Status ===


def get_relationship(user_id: int, target_id: int) -> Relationship:
    """Get the relationship status between two users."""
    return _get_manager().get_relationship(user_id, target_id)


def get_all_relationships(user_id: int) -> Dict[str, List]:
    """Get all relationships (friends, pending, blocked) for a user."""
    return _get_manager().get_all_relationships(user_id)


# === Mutual Information ===


def get_mutual_friends(user_id: int, target_id: int) -> List[int]:
    """Get list of mutual friend IDs between two users."""
    return _get_manager().get_mutual_friends(user_id, target_id)


def get_mutual_friend_count(user_id: int, target_id: int) -> int:
    """Get count of mutual friends between two users."""
    return _get_manager().get_mutual_friend_count(user_id, target_id)


def get_mutual_servers(user_id: int, target_id: int) -> List[int]:
    """Get list of mutual server IDs between two users."""
    return _get_manager().get_mutual_servers(user_id, target_id)


def get_mutual_server_count(user_id: int, target_id: int) -> int:
    """Get count of mutual servers between two users."""
    return _get_manager().get_mutual_server_count(user_id, target_id)


def get_mutual_info(user_id: int, target_id: int) -> MutualInfo:
    """Get all mutual information between two users."""
    return _get_manager().get_mutual_info(user_id, target_id)
