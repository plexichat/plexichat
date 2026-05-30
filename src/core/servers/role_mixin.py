"""Role operations mixin."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import Role


class RoleMixin:
    """Mixin for role operations.

    Provides: create_role, get_role, get_roles, update_role, delete_role, move_role
    """

    _manager: Any = None

    def create_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: bool = False,
        mentionable: bool = False,
    ) -> Role:
        """Create a new role in a server."""
        return self._manager.create_role(
            user_id, server_id, name, permissions, color, hoist, mentionable
        )

    def get_role(self, role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
        """Get a role by ID."""
        return self._manager.get_role(role_id, user_id)

    def get_roles(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
        """Get all roles in a server."""
        return self._manager.get_roles(user_id, server_id)

    def update_role(
        self,
        user_id: SnowflakeID,
        role_id: SnowflakeID,
        name: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
    ) -> Role:
        """Update role settings."""
        return self._manager.update_role(
            user_id, role_id, name, permissions, color, hoist, mentionable
        )

    def delete_role(self, user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
        """Delete a role."""
        return self._manager.delete_role(user_id, role_id)

    def move_role(
        self, user_id: SnowflakeID, role_id: SnowflakeID, position: int
    ) -> Role:
        """Move a role to a new position in hierarchy."""
        return self._manager.move_role(user_id, role_id, position)
