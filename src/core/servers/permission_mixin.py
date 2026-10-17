"""Permission operations mixin."""

from typing import Any, Dict, Optional

from src.core.base import SnowflakeID

from .models import ChannelOverride


class PermissionMixin:
    """Mixin for permission operations.

    Provides: get_channel_override, set_channel_override, delete_channel_override,
    has_permission, get_permissions, require_permission
    """

    _manager: Any = None

    def get_channel_override(
        self,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> Optional[ChannelOverride]:
        """Get permission override for a channel."""
        return self._manager.get_channel_override(channel_id, target_type, target_id)

    def set_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        allow: Optional[Dict[str, bool]] = None,
        deny: Optional[Dict[str, bool]] = None,
    ) -> ChannelOverride:
        """Set permission override for a channel."""
        return self._manager.set_channel_override(
            user_id, channel_id, target_type, target_id, allow, deny
        )

    def delete_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> bool:
        """Delete a permission override."""
        return self._manager.delete_channel_override(
            user_id, channel_id, target_type, target_id
        )

    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if a user has a permission in a server/channel."""
        return self._manager.has_permission(user_id, server_id, permission, channel_id)

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel."""
        return self._manager.get_permissions(user_id, server_id, channel_id)

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        """Require a permission, raising if not granted."""
        return self._manager.require_permission(
            user_id, server_id, permission, channel_id
        )
