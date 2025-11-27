"""
Voice module - Zero-friction API for voice channel state management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import voice
    voice.setup(db, auth, servers, relationships, presence)

    # In any other file (use directly)
    from src.core import voice
    state = voice.join_channel(user_id=1, channel_id=123)
"""

from typing import Optional, List, Dict, Any

from .models import (
    VoiceState,
    VoiceChannel,
    StageInstance,
    VoiceRegion,
    SpeakerRequest,
    VoiceChannelType,
)
from .exceptions import (
    VoiceError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelFullError,
    ChannelTypeError,
    UserNotInChannelError,
    UserAlreadyInChannelError,
    StageNotFoundError,
    SpeakerRequestNotFoundError,
    SpeakerRequestExistsError,
    NotSpeakerError,
    AlreadySpeakerError,
    PermissionDeniedError,
    InvalidVoiceStateError,
    UserNotFoundError,
)

__all__ = [
    # Models
    "VoiceState",
    "VoiceChannel",
    "StageInstance",
    "VoiceRegion",
    "SpeakerRequest",
    "VoiceChannelType",
    # Exceptions
    "VoiceError",
    "ChannelNotFoundError",
    "ChannelAccessDeniedError",
    "ChannelFullError",
    "ChannelTypeError",
    "UserNotInChannelError",
    "UserAlreadyInChannelError",
    "StageNotFoundError",
    "SpeakerRequestNotFoundError",
    "SpeakerRequestExistsError",
    "NotSpeakerError",
    "AlreadySpeakerError",
    "PermissionDeniedError",
    "InvalidVoiceStateError",
    "UserNotFoundError",
    # Setup
    "setup",
    # Channel operations
    "join_channel",
    "leave_channel",
    "move_to_channel",
    "get_channel_users",
    "get_voice_channel",
    "get_voice_channels",
    # Voice state operations
    "get_voice_state",
    "set_self_mute",
    "set_self_deaf",
    "set_streaming",
    "set_video",
    "update_voice_state",
    # Server moderation
    "server_mute",
    "server_unmute",
    "server_deaf",
    "server_undeaf",
    "move_member",
    "disconnect_member",
    # Stage channel operations
    "start_stage",
    "end_stage",
    "get_stage",
    "request_to_speak",
    "cancel_speak_request",
    "invite_to_speak",
    "move_to_audience",
    "get_speaker_requests",
    "get_speakers",
    "get_audience",
    # Channel settings
    "set_user_limit",
    "set_bitrate",
    "set_voice_region",
    "get_voice_regions",
    # AFK
    "set_afk_channel",
    "get_afk_channel",
    "check_afk_timeout",
    # User voice state queries
    "get_user_voice_state",
    "is_user_in_voice",
]

_manager = None
_setup_complete = False


def setup(db, auth_module=None, servers_module=None, relationships_module=None, presence_module=None):
    """
    Initialize the voice module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for user verification
        servers_module: Optional servers module for channel/permission checks
        relationships_module: Optional relationships module for block checks
        presence_module: Optional presence module for activity updates
    """
    global _manager, _setup_complete

    from .manager import VoiceManager

    _manager = VoiceManager(db, auth_module, servers_module, relationships_module, presence_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Voice module not initialized. Call voice.setup(db) first."
        )
    return _manager


# === Channel Operations ===


def join_channel(user_id: int, channel_id: int) -> VoiceState:
    """Join a voice channel."""
    return _get_manager().join_channel(user_id, channel_id)


def leave_channel(user_id: int) -> bool:
    """Leave current voice channel."""
    return _get_manager().leave_channel(user_id)


def move_to_channel(user_id: int, channel_id: int) -> VoiceState:
    """Move to a different voice channel."""
    return _get_manager().move_to_channel(user_id, channel_id)


def get_channel_users(channel_id: int) -> List[VoiceState]:
    """Get all users in a voice channel."""
    return _get_manager().get_channel_users(channel_id)


def get_voice_channel(channel_id: int, user_id: int) -> Optional[VoiceChannel]:
    """Get voice channel info."""
    return _get_manager().get_voice_channel(channel_id, user_id)


def get_voice_channels(user_id: int, server_id: int) -> List[VoiceChannel]:
    """Get all voice channels in a server."""
    return _get_manager().get_voice_channels(user_id, server_id)


# === Voice State Operations ===


def get_voice_state(user_id: int) -> Optional[VoiceState]:
    """Get user's current voice state."""
    return _get_manager().get_voice_state(user_id)


def set_self_mute(user_id: int, muted: bool) -> VoiceState:
    """Set self-mute state."""
    return _get_manager().set_self_mute(user_id, muted)


def set_self_deaf(user_id: int, deafened: bool) -> VoiceState:
    """Set self-deaf state."""
    return _get_manager().set_self_deaf(user_id, deafened)


def set_streaming(user_id: int, streaming: bool) -> VoiceState:
    """Set streaming (screen share) state."""
    return _get_manager().set_streaming(user_id, streaming)


def set_video(user_id: int, video: bool) -> VoiceState:
    """Set video (camera) state."""
    return _get_manager().set_video(user_id, video)


def update_voice_state(
    user_id: int,
    self_mute: Optional[bool] = None,
    self_deaf: Optional[bool] = None,
    streaming: Optional[bool] = None,
    video: Optional[bool] = None,
) -> VoiceState:
    """Update multiple voice state properties at once."""
    return _get_manager().update_voice_state(user_id, self_mute, self_deaf, streaming, video)


# === Server Moderation ===


def server_mute(moderator_id: int, target_user_id: int, server_id: int) -> VoiceState:
    """Server mute a user (moderator action)."""
    return _get_manager().server_mute(moderator_id, target_user_id, server_id)


def server_unmute(moderator_id: int, target_user_id: int, server_id: int) -> VoiceState:
    """Server unmute a user (moderator action)."""
    return _get_manager().server_unmute(moderator_id, target_user_id, server_id)


def server_deaf(moderator_id: int, target_user_id: int, server_id: int) -> VoiceState:
    """Server deafen a user (moderator action)."""
    return _get_manager().server_deaf(moderator_id, target_user_id, server_id)


def server_undeaf(moderator_id: int, target_user_id: int, server_id: int) -> VoiceState:
    """Server undeafen a user (moderator action)."""
    return _get_manager().server_undeaf(moderator_id, target_user_id, server_id)


def move_member(moderator_id: int, target_user_id: int, channel_id: int) -> VoiceState:
    """Move a member to a different voice channel (moderator action)."""
    return _get_manager().move_member(moderator_id, target_user_id, channel_id)


def disconnect_member(moderator_id: int, target_user_id: int, server_id: int) -> bool:
    """Disconnect a member from voice (moderator action)."""
    return _get_manager().disconnect_member(moderator_id, target_user_id, server_id)


# === Stage Channel Operations ===


def start_stage(user_id: int, channel_id: int, topic: str) -> StageInstance:
    """Start a stage instance in a stage channel."""
    return _get_manager().start_stage(user_id, channel_id, topic)


def end_stage(user_id: int, channel_id: int) -> bool:
    """End a stage instance."""
    return _get_manager().end_stage(user_id, channel_id)


def get_stage(channel_id: int) -> Optional[StageInstance]:
    """Get active stage instance for a channel."""
    return _get_manager().get_stage(channel_id)


def request_to_speak(user_id: int, channel_id: int) -> SpeakerRequest:
    """Request to speak in a stage channel (raise hand)."""
    return _get_manager().request_to_speak(user_id, channel_id)


def cancel_speak_request(user_id: int, channel_id: int) -> bool:
    """Cancel a request to speak."""
    return _get_manager().cancel_speak_request(user_id, channel_id)


def invite_to_speak(moderator_id: int, target_user_id: int, channel_id: int) -> VoiceState:
    """Invite a user to speak in a stage channel."""
    return _get_manager().invite_to_speak(moderator_id, target_user_id, channel_id)


def move_to_audience(moderator_id: int, target_user_id: int, channel_id: int) -> VoiceState:
    """Move a speaker to audience in a stage channel."""
    return _get_manager().move_to_audience(moderator_id, target_user_id, channel_id)


def get_speaker_requests(channel_id: int) -> List[SpeakerRequest]:
    """Get all pending speaker requests for a stage channel."""
    return _get_manager().get_speaker_requests(channel_id)


def get_speakers(channel_id: int) -> List[VoiceState]:
    """Get all speakers in a stage channel."""
    return _get_manager().get_speakers(channel_id)


def get_audience(channel_id: int) -> List[VoiceState]:
    """Get all audience members in a stage channel."""
    return _get_manager().get_audience(channel_id)


# === Channel Settings ===


def set_user_limit(user_id: int, channel_id: int, limit: int) -> VoiceChannel:
    """Set user limit for a voice channel (0 = unlimited)."""
    return _get_manager().set_user_limit(user_id, channel_id, limit)


def set_bitrate(user_id: int, channel_id: int, bitrate: int) -> VoiceChannel:
    """Set bitrate for a voice channel."""
    return _get_manager().set_bitrate(user_id, channel_id, bitrate)


def set_voice_region(user_id: int, channel_id: int, region_id: Optional[str]) -> VoiceChannel:
    """Set voice region for a channel (None = automatic)."""
    return _get_manager().set_voice_region(user_id, channel_id, region_id)


def get_voice_regions() -> List[VoiceRegion]:
    """Get available voice regions."""
    return _get_manager().get_voice_regions()


# === AFK ===


def set_afk_channel(user_id: int, server_id: int, channel_id: Optional[int], timeout_seconds: int = 300) -> bool:
    """Set AFK channel for a server."""
    return _get_manager().set_afk_channel(user_id, server_id, channel_id, timeout_seconds)


def get_afk_channel(server_id: int) -> Optional[int]:
    """Get AFK channel ID for a server."""
    return _get_manager().get_afk_channel(server_id)


def check_afk_timeout(user_id: int) -> Optional[VoiceState]:
    """Check and apply AFK timeout if needed. Returns new state if moved."""
    return _get_manager().check_afk_timeout(user_id)


# === User Voice State Queries ===


def get_user_voice_state(user_id: int) -> Optional[VoiceState]:
    """Get a user's current voice state if in a channel."""
    return _get_manager().get_voice_state(user_id)


def is_user_in_voice(user_id: int) -> bool:
    """Check if a user is currently in a voice channel."""
    return _get_manager().is_user_in_voice(user_id)
