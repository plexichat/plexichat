"""Role operations - create, get, update, delete, move roles."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import Role

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def create_role(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    name: str,
    permissions: Optional[Dict[str, bool]] = None,
    color: Optional[str] = None,
    hoist: bool = False,
    mentionable: bool = False,
) -> Role:
    """Create a new role in a server."""
    return _get_manager().create_role(
        user_id, server_id, name, permissions, color, hoist, mentionable
    )


def get_role(role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
    """Get a role by ID."""
    return _get_manager().get_role(role_id, user_id)


def get_roles(user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
    """Get all roles in a server."""
    return _get_manager().get_roles(user_id, server_id)


def update_role(
    user_id: SnowflakeID,
    role_id: SnowflakeID,
    name: Optional[str] = None,
    permissions: Optional[Dict[str, bool]] = None,
    color: Optional[str] = None,
    hoist: Optional[bool] = None,
    mentionable: Optional[bool] = None,
) -> Role:
    """Update role settings."""
    return _get_manager().update_role(
        user_id, role_id, name, permissions, color, hoist, mentionable
    )


def delete_role(user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
    """Delete a role."""
    return _get_manager().delete_role(user_id, role_id)


def move_role(user_id: SnowflakeID, role_id: SnowflakeID, position: int) -> Role:
    """Move a role to a new position in hierarchy."""
    return _get_manager().move_role(user_id, role_id, position)
