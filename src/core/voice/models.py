"""
Voice models - Dataclasses for all voice-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from src.core.base import SnowflakeID


class VoiceChannelType(Enum):
    """Types of voice channels."""

    VOICE = "voice"
    STAGE = "stage"


@dataclass
class VoiceState:
    """User's voice state in a channel."""

    user_id: SnowflakeID
    channel_id: SnowflakeID
    server_id: SnowflakeID
    self_mute: bool = False
    self_deaf: bool = False
    server_mute: bool = False
    server_deaf: bool = False
    suppress: bool = False
    streaming: bool = False
    video: bool = False
    joined_at: int = 0
    last_activity: int = 0


@dataclass
class VoiceChannel:
    """Voice channel with settings."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    channel_type: VoiceChannelType
    user_limit: int = 0
    bitrate: int = 64000
    region_id: Optional[str] = None
    position: int = 0
    category_id: Optional[SnowflakeID] = None
    user_count: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass
class StageInstance:
    """Active stage instance in a stage channel."""

    id: SnowflakeID
    channel_id: SnowflakeID
    server_id: SnowflakeID
    topic: str
    started_by: SnowflakeID
    started_at: int
    speaker_count: int = 0
    audience_count: int = 0


@dataclass
class SpeakerRequest:
    """Request to speak in a stage channel."""

    id: SnowflakeID
    user_id: SnowflakeID
    channel_id: SnowflakeID
    requested_at: int


@dataclass
class VoiceRegion:
    """Voice server region for WebRTC."""

    id: str
    name: str
    optimal: bool = False
    deprecated: bool = False
    custom: bool = False


# Default voice regions (placeholders for future WebRTC implementation)
DEFAULT_VOICE_REGIONS = [
    VoiceRegion(id="us-west", name="US West", optimal=False),
    VoiceRegion(id="us-east", name="US East", optimal=False),
    VoiceRegion(id="us-central", name="US Central", optimal=False),
    VoiceRegion(id="us-south", name="US South", optimal=False),
    VoiceRegion(id="eu-west", name="EU West", optimal=False),
    VoiceRegion(id="eu-central", name="EU Central", optimal=False),
    VoiceRegion(id="singapore", name="Singapore", optimal=False),
    VoiceRegion(id="japan", name="Japan", optimal=False),
    VoiceRegion(id="brazil", name="Brazil", optimal=False),
    VoiceRegion(id="sydney", name="Sydney", optimal=False),
    VoiceRegion(id="automatic", name="Automatic", optimal=True),
]


@dataclass
class AFKSettings:
    """AFK channel settings for a server."""

    server_id: SnowflakeID
    channel_id: Optional[SnowflakeID] = None
    timeout_seconds: int = 300
    created_at: int = 0
    updated_at: int = 0
