"""
Relationship models - Dataclasses for all relationship-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from src.core.base import SnowflakeID


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
    user_id: SnowflakeID
    target_user_id: SnowflakeID
    status: RelationshipStatus
    created_at: int = 0
    updated_at: int = 0


@dataclass
class FriendRequest:
    """Represents a friend request between users."""
    id: SnowflakeID
    sender_id: SnowflakeID
    recipient_id: SnowflakeID
    status: FriendRequestStatus
    message: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class BlockedUser:
    """Represents a blocked user relationship."""
    id: SnowflakeID
    blocker_id: SnowflakeID
    blocked_id: SnowflakeID
    reason: Optional[str] = None
    created_at: int = 0


@dataclass
class Friend:
    """Represents a friendship between users."""
    id: SnowflakeID
    user_id: SnowflakeID
    friend_id: SnowflakeID
    created_at: int = 0


@dataclass
class MutualInfo:
    """Information about mutual friends and servers."""
    mutual_friends: List[SnowflakeID]
    mutual_friend_count: int
    mutual_servers: List[SnowflakeID]
    mutual_server_count: int
