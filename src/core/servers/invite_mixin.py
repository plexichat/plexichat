"""Invite operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Invite, Member


class InviteMixin:
    """Mixin for invite operations.

    Provides: create_invite, get_invite, get_invites, use_invite, delete_invite
    """

    _manager: Any = None

    def create_invite(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel."""
        return self._manager.create_invite(
            user_id, channel_id, max_age, max_uses, temporary
        )

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        return self._manager.get_invite(code)

    def get_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Invite]:
        """Get all invites for a server."""
        return self._manager.get_invites(user_id, server_id)

    def use_invite(self, user_id: SnowflakeID, code: str) -> Member:
        """Use an invite to join a server."""
        return self._manager.use_invite(user_id, code)

    def delete_invite(self, user_id: SnowflakeID, code: str) -> bool:
        """Delete an invite."""
        return self._manager.delete_invite(user_id, code)
