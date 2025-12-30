"""
Messaging data models.

All models are dataclasses for clean, immutable data structures.
Uses Snowflake IDs for distributed unique identification.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum
from src.core.base import SnowflakeID


class ConversationType(Enum):
    """Type of conversation."""

    DM = "dm"
    GROUP = "group"
    NOTES = "notes"  # Personal notes - single user conversation
    THREAD = "thread"  # Dedicated conversation for a thread


class MessageType(Enum):
    """Type of message."""

    TEXT = "text"
    SYSTEM = "system"
    ATTACHMENT = "attachment"


class MessageStatusType(Enum):
    """Status of message delivery/read."""

    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"


class ParticipantRole(Enum):
    """Role of participant in a conversation."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class FilterAction(Enum):
    """Action to take when content matches filter."""

    BLOCK = "block"
    WARN = "warn"
    CENSOR = "censor"
    SPOILER = "spoiler"


@dataclass
class Conversation:
    """Conversation model (DM or group)."""

    id: SnowflakeID
    conversation_type: ConversationType
    created_at: int
    updated_at: int
    name: Optional[str] = None
    owner_id: Optional[SnowflakeID] = None
    max_participants: int = 100
    participant_count: int = 0
    last_message_id: Optional[SnowflakeID] = None
    last_message_at: Optional[int] = None
    encrypted: bool = False
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Participant:
    """Participant in a conversation."""

    id: SnowflakeID
    conversation_id: SnowflakeID
    user_id: SnowflakeID
    role: ParticipantRole
    joined_at: int
    last_read_message_id: Optional[SnowflakeID] = None
    last_read_at: Optional[int] = None
    muted: bool = False
    muted_until: Optional[int] = None
    permissions: Optional[Dict[str, bool]] = None
    nickname: Optional[str] = None


@dataclass
class Message:
    """Message model."""

    id: SnowflakeID
    conversation_id: SnowflakeID
    author_id: SnowflakeID
    content: str
    created_at: int
    updated_at: int
    content_encrypted: Optional[str] = None
    message_type: MessageType = MessageType.TEXT
    edited: bool = False
    edited_at: Optional[int] = None
    reply_to_id: Optional[SnowflakeID] = None
    pinned: bool = False
    pinned_at: Optional[int] = None
    pinned_by: Optional[SnowflakeID] = None
    attachments: List[Any] = field(default_factory=list)
    embeds: List[Any] = field(default_factory=list)
    reactions: List[Any] = field(default_factory=list)
    status: MessageStatusType = MessageStatusType.SENT
    delivery_count: int = 0
    read_count: int = 0
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    author_username: Optional[str] = None
    author_avatar_url: Optional[str] = None


@dataclass
class MessageStatus:
    """Delivery/read status for a message per user."""

    id: SnowflakeID
    message_id: SnowflakeID
    user_id: SnowflakeID
    status: MessageStatusType
    timestamp: int


@dataclass
class Attachment:
    """Message attachment model."""

    id: SnowflakeID
    message_id: SnowflakeID
    filename: str
    content_type: str
    size: int
    url: str
    created_at: int
    url_encrypted: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    deleted: bool = False


@dataclass
class ContentFilter:
    """User content filter settings."""

    user_id: SnowflakeID
    profanity_filter: bool = False
    nsfw_filter: bool = False
    spoiler_click_to_reveal: bool = True
    custom_blocked_words: List[str] = field(default_factory=list)
    filter_action: FilterAction = FilterAction.CENSOR


@dataclass
class UserMessageSettings:
    """User-specific message settings."""

    user_id: SnowflakeID
    allow_dms_from: str = "everyone"  # "everyone", "friends", "none"
    auto_create_dms: bool = True
    max_message_length: Optional[int] = None  # None = use global default
    max_attachment_size: Optional[int] = None  # None = use global default
    max_attachments_per_message: Optional[int] = None  # None = use global default
    read_receipts_enabled: bool = True
    typing_indicators_enabled: bool = True


@dataclass
class PinnedMessage:
    """Pinned message record."""

    id: SnowflakeID
    conversation_id: SnowflakeID
    message_id: SnowflakeID
    pinned_by: SnowflakeID
    pinned_at: int


@dataclass
class ConversationSummary:
    """Summary of conversation for listing."""

    id: SnowflakeID
    conversation_type: ConversationType
    name: Optional[str]
    participant_count: int
    last_message_at: Optional[int]
    unread_count: int = 0
    last_message_id: Optional[SnowflakeID] = None
    last_message_content: Optional[str] = None
    last_message_author: Optional[str] = None
    muted: bool = False
    encrypted: bool = False


# Rich text formatting markers
class TextFormat:
    """Text formatting markers for rich text."""

    BOLD = "**"
    ITALIC = "*"
    UNDERLINE = "__"
    STRIKETHROUGH = "~~"
    SPOILER = "||"
    CODE = "`"
    CODE_BLOCK = "```"
    QUOTE = "> "

    # Patterns for parsing
    PATTERNS = {
        "bold": r"\*\*(.+?)\*\*",
        "italic": r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
        "underline": r"__(.+?)__",
        "strikethrough": r"~~(.+?)~~",
        "spoiler": r"\|\|(.+?)\|\|",
        "code": r"`([^`]+)`",
        "code_block": r"```(\w*)\n?([\s\S]*?)```",
        "quote": r"^> (.+)$",
    }
