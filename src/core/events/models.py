"""
Event models - Dataclasses for all event types.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import time

from .types import EventType


@dataclass
class Event:
    """Base event class."""
    event_type: EventType
    data: Dict[str, Any]
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    server_id: Optional[int] = None
    channel_id: Optional[int] = None
    user_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return {
            "t": self.event_type.value,
            "d": self.data,
        }


@dataclass
class ReadyEvent(Event):
    """Ready event sent after successful identify."""
    session_id: str = ""
    user: Optional[Dict[str, Any]] = None
    guilds: List[Dict[str, Any]] = field(default_factory=list)
    resume_gateway_url: str = ""

    def __post_init__(self):
        self.event_type = EventType.READY
        self.data = {
            "v": 10,
            "user": self.user or {},
            "guilds": self.guilds,
            "session_id": self.session_id,
            "resume_gateway_url": self.resume_gateway_url,
        }


@dataclass
class MessageEvent(Event):
    """Message-related events."""
    message_id: int = 0
    content: Optional[str] = None
    author: Optional[Dict[str, Any]] = None
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    embeds: List[Dict[str, Any]] = field(default_factory=list)
    mentions: List[Dict[str, Any]] = field(default_factory=list)
    pinned: bool = False
    edited_timestamp: Optional[int] = None


@dataclass
class PresenceEvent(Event):
    """Presence update event."""
    status: str = "offline"
    activities: List[Dict[str, Any]] = field(default_factory=list)
    client_status: Optional[Dict[str, str]] = None

    def __post_init__(self):
        self.event_type = EventType.PRESENCE_UPDATE


@dataclass
class TypingEvent(Event):
    """Typing start event."""
    def __post_init__(self):
        self.event_type = EventType.TYPING_START


@dataclass
class ChannelEvent(Event):
    """Channel-related events."""
    name: Optional[str] = None
    channel_type: int = 0
    position: int = 0
    topic: Optional[str] = None
    nsfw: bool = False
    parent_id: Optional[int] = None


@dataclass
class GuildEvent(Event):
    """Guild/server-related events."""
    name: Optional[str] = None
    icon: Optional[str] = None
    owner_id: Optional[int] = None
    member_count: int = 0
    channels: List[Dict[str, Any]] = field(default_factory=list)
    roles: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GuildMemberEvent(Event):
    """Guild member events."""
    member_user_id: int = 0
    nick: Optional[str] = None
    roles: List[int] = field(default_factory=list)
    joined_at: Optional[int] = None


@dataclass
class VoiceStateEvent(Event):
    """Voice state update event."""
    voice_channel_id: Optional[int] = None
    self_mute: bool = False
    self_deaf: bool = False
    mute: bool = False
    deaf: bool = False

    def __post_init__(self):
        self.event_type = EventType.VOICE_STATE_UPDATE


@dataclass
class ReactionEvent(Event):
    """Reaction events."""
    message_id: int = 0
    emoji: Optional[Dict[str, Any]] = None
    member: Optional[Dict[str, Any]] = None
