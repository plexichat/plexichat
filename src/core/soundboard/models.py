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
    # The persisted schema column is ``cooldown_seconds``; the
    # Python API kwarg (``update_sound(..., cooldown=N)``) and the
    # primary field accessor are ``cooldown``.  To keep
    # ``dataclasses.asdict`` round-trips and any legacy
    # ``sound.cooldown_seconds`` consumer working, we declare
    # BOTH as concrete dataclass fields (a property alias would
    # be silently dropped by ``asdict``).  ``__post_init__`` keeps
    # them in lockstep so a future hand-rolled caller can pass
    # only one and have the other filled in.
    cooldown: int = 0
    cooldown_seconds: int = 0
    created_by: SnowflakeID = 0
    created_at: int = 0
    usage_count: int = 0

    def __post_init__(self) -> None:
        # If a caller supplied only one of the two aliases, mirror
        # it onto the other so the dataclass is internally
        # consistent.  If both are passed but disagree, raise
        # loudly — better to fail at construction than to silently
        # desync at JSON / websocket serialisation time.
        if self.cooldown_seconds == 0 and self.cooldown != 0:
            self.cooldown_seconds = self.cooldown
        elif self.cooldown == 0 and self.cooldown_seconds != 0:
            self.cooldown = self.cooldown_seconds
        elif self.cooldown != self.cooldown_seconds and not (
            self.cooldown == 0 and self.cooldown_seconds == 0
        ):
            raise ValueError(
                f"Sound.cooldown ({self.cooldown}) and "
                f"Sound.cooldown_seconds ({self.cooldown_seconds}) "
                "disagree; pass only one or pass matching values."
            )


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
