"""Member operations - add, get, update, remove members; kick, ban, unban; role assignments."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Member, Ban, Role

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def add_member(
    server_id: SnowflakeID,
    user_id: SnowflakeID,
    inviter_id: Optional[SnowflakeID] = None,
) -> Member:
    """Add a user as a member of a server."""
    return _get_manager().add_member(server_id, user_id, inviter_id)


def get_member(server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Member]:
    """Get a member by user ID."""
    return _get_manager().get_member(server_id, user_id)


def get_members(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    limit: int = 100,
    after_id: Optional[SnowflakeID] = None,
) -> List[Member]:
    """Get members of a server."""
    return _get_manager().get_members(user_id, server_id, limit, after_id)


def get_member_user_ids(
    server_id: SnowflakeID,
    exclude_user_id: Optional[SnowflakeID] = None,
) -> List[SnowflakeID]:
    """Get just the user IDs of server members."""
    return _get_manager().get_member_user_ids(server_id, exclude_user_id)


def get_all_shared_member_ids(user_id: SnowflakeID) -> List[SnowflakeID]:
    """Get IDs of all users who share at least one server with the given user."""
    return _get_manager().get_all_shared_member_ids(user_id)


def update_member(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    member_user_id: SnowflakeID,
    nickname: Optional[str] = None,
    muted: Optional[bool] = None,
    deafened: Optional[bool] = None,
    timeout_until: Optional[int] = None,
    timeout_reason: Optional[str] = None,
) -> Member:
    """Update member settings."""
    return _get_manager().update_member(
        user_id,
        server_id,
        member_user_id,
        nickname,
        muted,
        deafened,
        timeout_until,
        timeout_reason,
    )


def remove_member(user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
    """Remove yourself from a server (leave)."""
    return _get_manager().remove_member(user_id, server_id)


def leave_server(user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
    """Alias for remove_member - leave a server."""
    return _get_manager().leave_server(user_id, server_id)


def kick_member(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    member_user_id: SnowflakeID,
    reason: Optional[str] = None,
) -> bool:
    """Kick a member from a server."""
    return _get_manager().kick_member(user_id, server_id, member_user_id, reason)


def ban_member(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    member_user_id: SnowflakeID,
    reason: Optional[str] = None,
    delete_message_days: int = 0,
) -> Ban:
    """Ban a user from a server."""
    return _get_manager().ban_member(
        user_id, server_id, member_user_id, reason, delete_message_days
    )


def unban_member(
    user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
) -> bool:
    """Unban a user from a server."""
    return _get_manager().unban_member(user_id, server_id, banned_user_id)


def get_bans(user_id: SnowflakeID, server_id: SnowflakeID) -> List[Ban]:
    """Get all bans for a server."""
    return _get_manager().get_bans(user_id, server_id)


def assign_role(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    member_user_id: SnowflakeID,
    role_id: SnowflakeID,
) -> bool:
    """Assign a role to a member."""
    return _get_manager().assign_role(user_id, server_id, member_user_id, role_id)


def remove_role(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    member_user_id: SnowflakeID,
    role_id: SnowflakeID,
) -> bool:
    """Remove a role from a member."""
    return _get_manager().remove_role(user_id, server_id, member_user_id, role_id)


def get_member_roles(server_id: SnowflakeID, member_user_id: SnowflakeID) -> List[Role]:
    """Get all roles assigned to a member."""
    return _get_manager().get_member_roles(server_id, member_user_id)
