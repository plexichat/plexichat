"""Role assignment operations mixin."""

from typing import Any, List

from src.core.base import SnowflakeID

from .models import Role


class RoleAssignmentMixin:
    """Mixin for role assignment operations.

    Provides: assign_role, remove_role, get_member_roles
    """

    _manager: Any = None

    def assign_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Assign a role to a member."""
        return self._manager.assign_role(user_id, server_id, member_user_id, role_id)

    def remove_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Remove a role from a member."""
        return self._manager.remove_role(user_id, server_id, member_user_id, role_id)

    def get_member_roles(
        self, server_id: SnowflakeID, member_user_id: SnowflakeID
    ) -> List[Role]:
        """Get all roles assigned to a member."""
        return self._manager.get_member_roles(server_id, member_user_id)
