"""
Presence models - Dataclasses for all presence-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class UserStatus(Enum):
    """User online status."""
    ONLINE = "online"
    IDLE = "idle"
    DND = "dnd"
    INVISIBLE = "invisible"
    OFFLINE = "offline"


class ActivityType(Enum):
    """Type of user activity."""
    PLAYING = "playing"
    STREAMING = "streaming"
    LISTENING = "listening"
    WATCHING = "watching"
    COMPETING = "competing"
    CUSTOM = "custom"


@dataclass
class CustomStatus:
    """User's custom status message."""
    text: str
    emoji: Optional[str] = None
    expires_at: Optional[int] = None
    created_at: int = 0


@dataclass
class Activity:
    """User's current activity."""
    activity_type: ActivityType
    name: str
    details: Optional[str] = None
    url: Optional[str] = None
    state: Optional[str] = None
    start_timestamp: Optional[int] = None
    end_timestamp: Optional[int] = None
    large_image: Optional[str] = None
    large_text: Optional[str] = None
    small_image: Optional[str] = None
    small_text: Optional[str] = None
    created_at: int = 0


@dataclass
class TypingIndicator:
    """Typing indicator for a user in a channel."""
    user_id: int
    channel_id: int
    started_at: int
    expires_at: int


@dataclass
class Presence:
    """Full presence information for a user."""
    user_id: int
    status: UserStatus
    custom_status: Optional[CustomStatus] = None
    activity: Optional[Activity] = None
    last_seen: int = 0
    updated_at: int = 0
