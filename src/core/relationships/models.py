"""
Relationship models - Dataclasses for all relationship-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List


class RelationshipStatus(Enum):
    """Status of relationship between two users."""
    NONE = "none"
    FRIEND = "friend"
    BLOCKED = "blocked"
    PENDING_INCOMING = "pending_incoming"
    PENDING_OUTGOING = "pending_outgoing"


class FriendRequestStatus(Enum):
    """Status of a friend request."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"


@dataclass
class Relationship:
    """Represents the relationship between two users."""
    user_id: int
    target_user_id: int
    status: RelationshipStatus
    created_at: int = 0
    updated_at: int = 0


@dataclass
class FriendRequest:
    """Represents a friend request between users."""
    id: int
    sender_id: int
    recipient_id: int
    status: FriendRequestStatus
    message: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class BlockedUser:
    """Represents a blocked user relationship."""
    id: int
    blocker_id: int
    blocked_id: int
    reason: Optional[str] = None
    created_at: int = 0


@dataclass
class Friend:
    """Represents a friendship between users."""
    id: int
    user_id: int
    friend_id: int
    created_at: int = 0


@dataclass
class MutualInfo:
    """Information about mutual friends and servers."""
    mutual_friends: List[int]
    mutual_friend_count: int
    mutual_servers: List[int]
    mutual_server_count: int
