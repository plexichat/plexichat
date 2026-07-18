"""Permission operations - check permissions, get permissions, require permissions, channel overrides."""

from typing import Any, Dict, Optional

from src.core.base import SnowflakeID

from .models import ChannelOverride

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def get_channel_override(
    channel_id: SnowflakeID,
    target_type: str,
    target_id: SnowflakeID,
) -> Optional[ChannelOverride]:
    """Get permission override for a channel."""
    return _get_manager().get_channel_override(channel_id, target_type, target_id)


def set_channel_override(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    target_type: str,
    target_id: SnowflakeID,
    allow: Optional[Dict[str, bool]] = None,
    deny: Optional[Dict[str, bool]] = None,
) -> ChannelOverride:
    """Set permission override for a channel."""
    return _get_manager().set_channel_override(
        user_id, channel_id, target_type, target_id, allow, deny
    )


def delete_channel_override(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    target_type: str,
    target_id: SnowflakeID,
) -> bool:
    """Delete a permission override."""
    return _get_manager().delete_channel_override(
        user_id, channel_id, target_type, target_id
    )


def has_permission(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    permission: str,
    channel_id: Optional[SnowflakeID] = None,
) -> bool:
    """Check if a user has a permission in a server/channel."""
    return _get_manager().has_permission(user_id, server_id, permission, channel_id)


def get_permissions(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    channel_id: Optional[SnowflakeID] = None,
) -> Dict[str, bool]:
    """Get all permissions for a user in a server/channel."""
    return _get_manager().get_permissions(user_id, server_id, channel_id)


def require_permission(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    permission: str,
    channel_id: Optional[SnowflakeID] = None,
) -> None:
    """Require a permission, raising if not granted."""
    return _get_manager().require_permission(user_id, server_id, permission, channel_id)
