"""
Notification models - Dataclasses for all notification-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class MentionType(Enum):
    """Types of mentions that can appear in messages."""
    USER = "user"
    ROLE = "role"
    EVERYONE = "everyone"
    HERE = "here"
    CHANNEL = "channel"


class NotificationType(Enum):
    """Types of notifications."""
    MESSAGE = "message"
    MENTION = "mention"
    FRIEND_REQUEST = "friend_request"
    SERVER_INVITE = "server_invite"
    REACTION = "reaction"
    SYSTEM = "system"


class NotificationLevel(Enum):
    """Notification level settings."""
    ALL_MESSAGES = "all"
    ONLY_MENTIONS = "mentions"
    NOTHING = "nothing"
    MUTED = "muted"


@dataclass
class Mention:
    """Represents a parsed mention from message content."""
    mention_type: MentionType
    target_id: Optional[int] = None
    raw_text: str = ""
    start_pos: int = 0
    end_pos: int = 0
    valid: bool = True
    error: Optional[str] = None


@dataclass
class MentionPosition:
    """Position of a mention in message content for highlighting."""
    start: int
    end: int
    mention_type: MentionType
    is_self: bool = False


@dataclass
class Notification:
    """Represents a notification for a user."""
    id: int
    user_id: int
    sender_id: int
    message_id: int
    conversation_id: int
    server_id: Optional[int] = None
    channel_id: Optional[int] = None
    mention_type: MentionType = MentionType.USER
    content_preview: str = ""
    read: bool = False
    created_at: int = 0


@dataclass
class NotificationSettings:
    """User notification settings (global or per-server)."""
    user_id: int
    server_id: Optional[int] = None
    level: NotificationLevel = NotificationLevel.ALL_MESSAGES
    dm_notifications: bool = True
    suppress_everyone: bool = False
    suppress_roles: bool = False
    mobile_push: bool = True
    created_at: int = 0
    updated_at: int = 0


@dataclass
class ChannelNotificationOverride:
    """Per-channel notification override."""
    user_id: int
    channel_id: int
    level: NotificationLevel = NotificationLevel.ALL_MESSAGES
    muted_until: Optional[int] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class UnreadCount:
    """Unread message and mention counts."""
    total_unread: int = 0
    mention_count: int = 0
    server_id: Optional[int] = None
    channel_id: Optional[int] = None


@dataclass
class NotificationFeed:
    """Collection of notifications for feed display."""
    notifications: List[Notification] = field(default_factory=list)
    total_count: int = 0
    unread_count: int = 0
    has_more: bool = False


@dataclass
class PushPayload:
    """Push notification payload (prepared but not sent)."""
    user_id: int
    title: str
    body: str
    data: Dict[str, Any] = field(default_factory=dict)
    badge_count: int = 0
    sound: str = "default"
    priority: str = "high"
