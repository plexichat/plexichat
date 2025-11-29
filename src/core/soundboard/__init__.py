"""
Soundboard module - Zero-friction API for server soundboard.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import soundboard
    soundboard.setup(db, servers)

    # In any other file (use directly)
    from src.core import soundboard
    sound = soundboard.upload_sound(user_id, server_id, "airhorn", format, url, size, duration)
"""

from typing import Optional, List

from .models import (
    Sound,
    SoundPermissions,
    SoundCooldown,
    SoundUsage,
    SoundPlayback,
    SoundFormat,
)
from .exceptions import (
    SoundboardError,
    SoundNotFoundError,
    SoundLimitError,
    InvalidSoundFormatError,
    SoundTooLargeError,
    SoundTooLongError,
    InvalidSoundNameError,
    SoundCooldownError,
    PermissionDeniedError,
    ServerNotFoundError,
    ChannelNotFoundError,
    NotInVoiceChannelError,
)

__all__ = [
    "Sound",
    "SoundPermissions",
    "SoundCooldown",
    "SoundUsage",
    "SoundPlayback",
    "SoundFormat",
    "SoundboardError",
    "SoundNotFoundError",
    "SoundLimitError",
    "InvalidSoundFormatError",
    "SoundTooLargeError",
    "SoundTooLongError",
    "InvalidSoundNameError",
    "SoundCooldownError",
    "PermissionDeniedError",
    "ServerNotFoundError",
    "ChannelNotFoundError",
    "NotInVoiceChannelError",
    "setup",
    "upload_sound",
    "get_sound",
    "get_server_sounds",
    "delete_sound",
    "set_sound_permissions",
    "play_sound",
]

_manager = None
_setup_complete = False


def setup(db, servers_module=None):
    """
    Initialize the soundboard module.

    Args:
        db: Database instance (must be connected)
        servers_module: Optional servers module for permission checks
    """
    global _manager, _setup_complete

    from .manager import SoundboardManager

    _manager = SoundboardManager(db, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Soundboard module not initialized. Call soundboard.setup(db) first."
        )
    return _manager


def upload_sound(
    user_id: int,
    server_id: int,
    name: str,
    format: SoundFormat,
    url: str,
    size: int,
    duration_seconds: float,
    emoji: Optional[str] = None,
    volume: float = 1.0,
) -> Sound:
    """Upload a sound to server soundboard."""
    return _get_manager().upload_sound(
        user_id, server_id, name, format, url, size, duration_seconds, emoji, volume
    )


def get_sound(sound_id: int, user_id: int) -> Optional[Sound]:
    """Get a sound by ID."""
    return _get_manager().get_sound(sound_id, user_id)


def get_server_sounds(user_id: int, server_id: int) -> List[Sound]:
    """Get all sounds for a server."""
    return _get_manager().get_server_sounds(user_id, server_id)


def delete_sound(user_id: int, sound_id: int) -> bool:
    """Delete a sound."""
    return _get_manager().delete_sound(user_id, sound_id)


def set_sound_permissions(
    user_id: int,
    sound_id: int,
    role_id: int,
    can_use: bool
) -> SoundPermissions:
    """Set sound usage permissions for a role."""
    return _get_manager().set_sound_permissions(user_id, sound_id, role_id, can_use)


def play_sound(user_id: int, sound_id: int, channel_id: int) -> SoundPlayback:
    """Play a sound in a voice channel."""
    return _get_manager().play_sound(user_id, sound_id, channel_id)
