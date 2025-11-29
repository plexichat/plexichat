"""
Soundboard models - Dataclasses for all soundboard-related entities.
"""

from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum


class SoundFormat(Enum):
    """Sound file format."""
    MP3 = "mp3"
    OGG = "ogg"


@dataclass
class Sound:
    """Represents a soundboard sound."""
    id: int
    server_id: int
    name: str
    format: SoundFormat = SoundFormat.MP3
    emoji: Optional[str] = None
    url: str = ""
    size: int = 0
    duration_seconds: float = 0.0
    volume: float = 1.0
    created_by: int = 0
    created_at: int = 0
    usage_count: int = 0


@dataclass
class SoundPermissions:
    """Sound usage permissions per role."""
    id: int
    sound_id: int
    role_id: int
    can_use: bool = True


@dataclass
class SoundCooldown:
    """Sound cooldown tracking per user."""
    user_id: int
    sound_id: int
    last_used_at: int
    cooldown_seconds: int


@dataclass
class SoundUsage:
    """Tracks sound usage statistics."""
    id: int
    sound_id: int
    user_id: int
    channel_id: int
    used_at: int


@dataclass
class SoundPlayback:
    """Sound playback event."""
    sound: Sound
    user_id: int
    channel_id: int
    timestamp: int
