"""Member operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Member, Ban


class MemberMixin:
    """Mixin for member operations.

    Provides: add_member, get_member, get_members, get_member_user_ids,
    get_all_shared_member_ids, update_member, remove_member, leave_server,
    kick_member, ban_member, unban_member, get_bans
    """

    _manager: Any = None

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
    ) -> Member:
        """Add a user as a member of a server."""
        return self._manager.add_member(server_id, user_id, inviter_id)

    def get_member(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Member]:
        """Get a member by user ID."""
        return self._manager.get_member(server_id, user_id)

    def get_members(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 100,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Member]:
        """Get members of a server."""
        return self._manager.get_members(user_id, server_id, limit, after_id)

    def get_member_user_ids(
        self,
        server_id: SnowflakeID,
        exclude_user_id: Optional[SnowflakeID] = None,
    ) -> List[SnowflakeID]:
        """Get just the user IDs of server members."""
        return self._manager.get_member_user_ids(server_id, exclude_user_id)

    def get_all_shared_member_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Get IDs of all users who share at least one server with the given user."""
        return self._manager.get_all_shared_member_ids(user_id)

    def update_member(
        self,
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
        return self._manager.update_member(
            user_id,
            server_id,
            member_user_id,
            nickname,
            muted,
            deafened,
            timeout_until,
            timeout_reason,
        )

    def remove_member(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        """Remove yourself from a server (leave)."""
        return self._manager.remove_member(user_id, server_id)

    def leave_server(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        """Alias for remove_member - leave a server."""
        return self._manager.remove_member(user_id, server_id)

    def kick_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> bool:
        """Kick a member from a server."""
        return self._manager.kick_member(user_id, server_id, member_user_id, reason)

    def ban_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
        delete_message_days: int = 0,
    ) -> Ban:
        """Ban a user from a server."""
        return self._manager.ban_member(
            user_id, server_id, member_user_id, reason, delete_message_days
        )

    def unban_member(
        self, user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
    ) -> bool:
        """Unban a user from a server."""
        return self._manager.unban_member(user_id, server_id, banned_user_id)

    def get_bans(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Ban]:
        """Get all bans for a server."""
        return self._manager.get_bans(user_id, server_id)
