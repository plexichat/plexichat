"""Channel operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Channel, ChannelCategory, ChannelType


class ChannelMixin:
    """Mixin for channel operations.

    Provides: create_channel, create_category, get_channel, get_channels,
    update_channel, delete_channel, move_channel
    """

    _manager: Any = None

    def create_channel(
        self,
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
        return self._manager.create_channel(
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
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
    ) -> ChannelCategory:
        """Create a new channel category."""
        return self._manager.create_category(user_id, server_id, name)

    def get_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Channel]:
        """Get a channel by ID if user has access."""
        return self._manager.get_channel(user_id, channel_id)

    def get_channels(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        return self._manager.get_channels(user_id, server_id, channel_type)

    def update_channel(
        self,
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
        return self._manager.update_channel(
            user_id,
            channel_id,
            name,
            topic,
            nsfw,
            slowmode_seconds,
            read_receipts_enabled,
            category_id,
        )

    def delete_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        """Delete a channel."""
        return self._manager.delete_channel(user_id, channel_id)

    def move_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, position: int
    ) -> Channel:
        """Move a channel to a new position."""
        return self._manager.move_channel(user_id, channel_id, position)
