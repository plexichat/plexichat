"""
Reaction models - Dataclasses for all reaction-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from src.core.base import SnowflakeID


@dataclass
class CustomEmoji:
    """Represents a custom emoji from a server."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    animated: bool = False
    url: str = ""
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    created_by: SnowflakeID = 0
    available: bool = True
    created_at: int = 0
    uploader_username: Optional[str] = None


@dataclass
class Reaction:
    """Represents a single reaction by a user on a message."""

    id: SnowflakeID
    message_id: SnowflakeID
    user_id: SnowflakeID
    emoji: str
    is_custom: bool = False
    custom_emoji_id: Optional[SnowflakeID] = None
    created_at: int = 0


@dataclass
class ReactionCount:
    """Represents aggregated reaction count for an emoji on a message."""

    message_id: SnowflakeID
    emoji: str
    count: int
    is_custom: bool = False
    custom_emoji_id: Optional[SnowflakeID] = None
    me: bool = False
    url: Optional[str] = None


@dataclass
class ReactionUser:
    """Represents a user who reacted with a specific emoji."""

    user_id: SnowflakeID
    reacted_at: int


@dataclass
class MessageReactions:
    """All reactions on a message with counts and user info."""

    message_id: SnowflakeID
    reactions: List[ReactionCount] = field(default_factory=list)
    total_count: int = 0
