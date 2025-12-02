"""
Reaction models - Dataclasses for all reaction-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class CustomEmoji:
    """Represents a custom emoji from a server."""
    id: int
    server_id: int
    name: str
    animated: bool = False
    url: str = ""
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    created_by: int = 0
    available: bool = True
    created_at: int = 0


@dataclass
class Reaction:
    """Represents a single reaction by a user on a message."""
    id: int
    message_id: int
    user_id: int
    emoji: str
    is_custom: bool = False
    custom_emoji_id: Optional[int] = None
    created_at: int = 0


@dataclass
class ReactionCount:
    """Represents aggregated reaction count for an emoji on a message."""
    message_id: int
    emoji: str
    count: int
    is_custom: bool = False
    custom_emoji_id: Optional[int] = None
    me: bool = False


@dataclass
class ReactionUser:
    """Represents a user who reacted with a specific emoji."""
    user_id: int
    reacted_at: int


@dataclass
class MessageReactions:
    """All reactions on a message with counts and user info."""
    message_id: int
    reactions: List[ReactionCount] = field(default_factory=list)
    total_count: int = 0
