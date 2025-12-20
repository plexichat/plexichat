"""
Messaging data models.

All models are dataclasses for clean, immutable data structures.
Uses Snowflake IDs for distributed unique identification.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum


class ConversationType(Enum):
    """Type of conversation."""

    DM = "dm"
    GROUP = "group"
    NOTES = "notes"  # Personal notes - single user conversation


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

    id: int
    conversation_type: ConversationType
    created_at: int
    updated_at: int
    name: Optional[str] = None
    owner_id: Optional[int] = None
    max_participants: int = 100
    participant_count: int = 0
    last_message_id: Optional[int] = None
    last_message_at: Optional[int] = None
    encrypted: bool = False
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Participant:
    """Participant in a conversation."""

    id: int
    conversation_id: int
    user_id: int
    role: ParticipantRole
    joined_at: int
    last_read_message_id: Optional[int] = None
    last_read_at: Optional[int] = None
    muted: bool = False
    muted_until: Optional[int] = None
    permissions: Optional[Dict[str, bool]] = None
    nickname: Optional[str] = None


@dataclass
class Message:
    """Message model."""

    id: int
    conversation_id: int
    author_id: int
    content: str
    created_at: int
    updated_at: int
    content_encrypted: Optional[str] = None
    message_type: MessageType = MessageType.TEXT
    edited: bool = False
    deleted: bool = False
    deleted_at: Optional[int] = None
    reply_to_id: Optional[int] = None
    pinned: bool = False
    pinned_at: Optional[int] = None
    pinned_by: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

    # Populated on fetch, not stored directly
    attachments: List["Attachment"] = field(default_factory=list)
    status: Optional[MessageStatusType] = None
    delivery_count: int = 0
    read_count: int = 0

    @property
    def embeds(self) -> List[Dict[str, Any]]:
        """Get embeds from metadata."""
        if self.metadata and "embeds" in self.metadata:
            return self.metadata["embeds"]
        return []


@dataclass
class MessageStatus:
    """Delivery/read status for a message per user."""

    id: int
    message_id: int
    user_id: int
    status: MessageStatusType
    timestamp: int


@dataclass
class Attachment:
    """Message attachment model."""

    id: int
    message_id: int
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

    user_id: int
    profanity_filter: bool = False
    nsfw_filter: bool = False
    spoiler_click_to_reveal: bool = True
    custom_blocked_words: List[str] = field(default_factory=list)
    filter_action: FilterAction = FilterAction.CENSOR


@dataclass
class UserMessageSettings:
    """User-specific message settings."""

    user_id: int
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

    id: int
    conversation_id: int
    message_id: int
    pinned_by: int
    pinned_at: int


@dataclass
class ConversationSummary:
    """Summary of conversation for listing."""

    id: int
    conversation_type: ConversationType
    name: Optional[str]
    participant_count: int
    last_message_preview: Optional[str]
    last_message_at: Optional[int]
    unread_count: int
    muted: bool


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
