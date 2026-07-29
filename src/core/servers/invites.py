"""Invite operations - create, get, use, delete invites."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Invite, Member

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def create_invite(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    max_age: int = 86400,
    max_uses: int = 0,
    temporary: bool = False,
) -> Invite:
    """Create an invite to a channel."""
    return _get_manager().create_invite(
        user_id, channel_id, max_age, max_uses, temporary
    )


def get_invite(code: str) -> Optional[Invite]:
    """Get an invite by code."""
    return _get_manager().get_invite(code)


def get_invites(user_id: SnowflakeID, server_id: SnowflakeID) -> List[Invite]:
    """Get all invites for a server."""
    return _get_manager().get_invites(user_id, server_id)


def use_invite(user_id: SnowflakeID, code: str) -> Member:
    """Use an invite to join a server."""
    return _get_manager().use_invite(user_id, code)


def delete_invite(user_id: SnowflakeID, code: str) -> bool:
    """Delete an invite."""
    return _get_manager().delete_invite(user_id, code)
