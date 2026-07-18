"""Channel operations - create, get, update, delete, and move channels."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Channel, ChannelCategory, ChannelType

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def create_channel(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    name: str,
    channel_type: ChannelType = ChannelType.TEXT,
    category_id: Optional[SnowflakeID] = None,
    topic: Optional[str] = None,
    nsfw: bool = False,
    slowmode_seconds: int = 0,
    read_receipts_enabled: bool = True,
) -> Channel:
    """Create a new channel in a server."""
    return _get_manager().create_channel(
        user_id,
        server_id,
        name,
        channel_type,
        category_id,
        topic,
        nsfw,
        slowmode_seconds,
        read_receipts_enabled,
    )


def create_category(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    name: str,
) -> ChannelCategory:
    """Create a new channel category."""
    return _get_manager().create_category(user_id, server_id, name)


def get_channel(channel_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Channel]:
    """Get a channel by ID if user has access."""
    return _get_manager().get_channel(channel_id, user_id)


def channel_exists(channel_id: SnowflakeID) -> bool:
    """Membership-agnostic existence probe.

    Returns ``True`` if the channel row exists at all, regardless
    of whether the caller is a member or has any permission.
    Use this to distinguish ``404`` (channel gone) from ``403``
    (exists, caller blocked) in the channels API: pre-check
    ``channel_exists`` first; only treat a non-existent channel
    as ``404``; treat a missing ``get_channel`` result for an
    existing channel as ``403``. Single ``SELECT 1`` query — cheap
    enough to call on every PATCH / invite request without load
    concerns.
    """
    return _get_manager().channel_exists(channel_id)


def get_channels(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    channel_type: Optional[ChannelType] = None,
) -> List[Channel]:
    """Get all channels in a server."""
    return _get_manager().get_channels(user_id, server_id, channel_type)


def update_channel(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    name: Optional[str] = None,
    topic: Optional[str] = None,
    nsfw: Optional[bool] = None,
    slowmode_seconds: Optional[int] = None,
    read_receipts_enabled: Optional[bool] = None,
    category_id: Optional[SnowflakeID] = None,
) -> Channel:
    """Update channel settings."""
    return _get_manager().update_channel(
        user_id,
        channel_id,
        name,
        topic,
        nsfw,
        slowmode_seconds,
        read_receipts_enabled,
        category_id,
    )


def delete_channel(user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
    """Delete a channel."""
    return _get_manager().delete_channel(user_id, channel_id)


def move_channel(
    user_id: SnowflakeID, channel_id: SnowflakeID, position: int
) -> Channel:
    """Move a channel to a new position."""
    return _get_manager().move_channel(user_id, channel_id, position)
