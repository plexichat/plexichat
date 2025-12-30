"""
Soundboard models - Dataclasses for all soundboard-related entities.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum
from src.core.base import SnowflakeID


class SoundFormat(Enum):
    """Sound file format."""
    MP3 = "mp3"
    OGG = "ogg"


@dataclass
class Sound:
    """Represents a soundboard sound."""
    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    format: SoundFormat = SoundFormat.MP3
    emoji: Optional[str] = None
    url: str = ""
    size: int = 0
    duration_seconds: float = 0.0
    volume: float = 1.0
    created_by: SnowflakeID = 0
    created_at: int = 0
    usage_count: int = 0


@dataclass
class SoundPermissions:
    """Sound usage permissions per role."""
    id: SnowflakeID
    sound_id: SnowflakeID
    role_id: SnowflakeID
    can_use: bool = True


@dataclass
class SoundCooldown:
    """Sound cooldown tracking per user."""
    user_id: SnowflakeID
    sound_id: SnowflakeID
    last_used_at: int
    cooldown_seconds: int


@dataclass
class SoundUsage:
    """Tracks sound usage statistics."""
    id: SnowflakeID
    sound_id: SnowflakeID
    user_id: SnowflakeID
    channel_id: SnowflakeID
    used_at: int


@dataclass
class SoundPlayback:
    """Sound playback event."""
    sound: Sound
    user_id: SnowflakeID
    channel_id: SnowflakeID
    timestamp: int
