"""Server operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Server


class ServerMixin:
    """Mixin for server operations.

    Provides: create_server, get_server, get_servers, server_exists,
    update_server, delete_server, transfer_ownership
    """

    _manager: Any = None

    def create_server(
        self,
        owner_id: SnowflakeID,
        name: str,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
    ) -> Server:
        """Create a new server."""
        return self._manager.create_server(owner_id, name, description, icon_url)

    def get_server(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Server]:
        """Get a server by ID if user has access."""
        return self._manager.get_server(server_id, user_id)

    def get_servers(self, user_id: SnowflakeID, limit: int = 100) -> List[Server]:
        """Get all servers a user is a member of."""
        return self._manager.get_servers(user_id, limit)

    def server_exists(self, server_id: SnowflakeID) -> bool:
        """Check if a server exists by ID (without permission check)."""
        return self._manager.server_exists(server_id)

    def update_server(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        default_channel_id: Optional[SnowflakeID] = None,
    ) -> Server:
        """Update server settings."""
        return self._manager.update_server(
            user_id, server_id, name, description, icon_url, default_channel_id
        )

    def delete_server(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        """Delete a server (owner only)."""
        return self._manager.delete_server(user_id, server_id)

    def transfer_ownership(
        self, user_id: SnowflakeID, server_id: SnowflakeID, new_owner_id: SnowflakeID
    ) -> Server:
        """Transfer server ownership to another member."""
        return self._manager.transfer_ownership(user_id, server_id, new_owner_id)
