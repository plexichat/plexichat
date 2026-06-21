import secrets
from typing import Optional, List, Dict, Any, Union

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from ..models import (
    Channel,
    ChannelCategory,
    Role,
    Member,
    ChannelOverride,
    Invite,
    Ban,
    ChannelType,
    AuditLogAction,
)
from ..exceptions import (
    ServerAccessDeniedError,
    PermissionDeniedError,
    InvalidChannelNameError,
    OwnerCannotLeaveError,
    MemberExistsError,
    InviteNotFoundError,
    InviteMaxUsesError,
    UserBannedError,
    BanNotFoundError,
    BanExistsError,
    RoleHierarchyError,
    DefaultRoleError,
    InvalidRoleNameError,
    CannotModifyOwnerError,
)
from .converters import (
    _row_to_channel,
)

from .server_ops import ServerOpsMixin
from .channel_ops import ChannelOpsMixin
from .audit_ops import AuditOpsMixin
from .cache_ops import CacheOpsMixin
from .member_ops import MemberOpsMixin
from .permission_ops import PermissionOpsMixin
from .invite_ops import InviteOpsMixin


class ServerManager(
    ServerOpsMixin,
    ChannelOpsMixin,
    AuditOpsMixin,
    CacheOpsMixin,
    MemberOpsMixin,
    PermissionOpsMixin,
    InviteOpsMixin,
    BaseManager,
):
    """Core server manager handling all operations."""

    ChannelType = ChannelType
    AuditLogAction = AuditLogAction

    ServerAccessDeniedError = ServerAccessDeniedError
    PermissionDeniedError = PermissionDeniedError
    InvalidChannelNameError = InvalidChannelNameError
    OwnerCannotLeaveError = OwnerCannotLeaveError
    CannotModifyOwnerError = CannotModifyOwnerError
    MemberExistsError = MemberExistsError
    InviteNotFoundError = InviteNotFoundError
    InviteMaxUsesError = InviteMaxUsesError
    UserBannedError = UserBannedError
    BanNotFoundError = BanNotFoundError
    BanExistsError = BanExistsError
    RoleHierarchyError = RoleHierarchyError
    DefaultRoleError = DefaultRoleError
    InvalidRoleNameError = InvalidRoleNameError

    def _get_manager(self):
        """Compatibility helper for older tests and call sites."""
        return self

    def __init__(self, db, auth_module=None, messaging_module=None):
        """Initialize the server manager."""
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._config = self._load_config()
        self._encrypt_descriptions = config.get(
            "encryption.encrypt_descriptions", False
        )
        self._encrypt_thread_names = config.get(
            "encryption.encrypt_thread_names", False
        )
        self.instance_id = secrets.token_hex(4)

        self._cache_ttl = 60

        self._member_cache_prefix = "srv_member:"
        self._permission_cache_prefix = "srv_permission:"
        self._channel_cache_prefix = "srv_channel:"
        self._server_owner_cache_prefix = "srv_owner:"
        self._member_roles_cache_prefix = "srv_member_roles:"

        from ..handlers.audit_handler import AuditHandler
        from ..handlers.channel_handler import ChannelHandler
        from ..handlers.role_handler import RoleHandler
        from ..handlers.member_handler import MemberHandler

        self.audit_handler = AuditHandler(self)
        self.channel_handler = ChannelHandler(self)
        self.role_handler = RoleHandler(self)
        self.member_handler = MemberHandler(self)

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
            "max_roles_per_server": 250,
            "max_members_per_server": 250000,
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_min_length": 1,
            "channel_name_max_length": 100,
            "role_name_min_length": 1,
            "role_name_max_length": 100,
            "invite_code_length": 8,
        }

        server_config = config.get("servers", {})
        return {**defaults, **server_config}

    def _log_audit(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[SnowflakeID] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Log an audit entry."""
        self.audit_handler.log_audit(
            server_id, user_id, action, target_type, target_id, changes, reason
        )

    # === Channel Operations ===

    def create_channel(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        channel_type: Union[ChannelType, str] = ChannelType.TEXT,
        category_id: Optional[SnowflakeID] = None,
        topic: Optional[str] = None,
        nsfw: bool = False,
        slowmode_seconds: int = 0,
        read_receipts_enabled: bool = True,
    ) -> Channel:
        """Create a new channel in a server."""
        return self.channel_handler.create_channel(
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
        return self.channel_handler.create_category(user_id, server_id, name)

    def delete_category(self, user_id: SnowflakeID, category_id: SnowflakeID) -> bool:
        """Delete a channel category."""
        return self.channel_handler.delete_category(user_id, category_id)

    def get_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Channel]:
        """Get a channel by ID with caching.

        Canonical signature: ``(channel_id, user_id)`` — same order
        as the public :class:`ServersManager` exposed via
        ``src/core/servers/__init__.py`` and the channel-mixin
        convenience wrapper.  The prior ``(user_id, channel_id)``
        order was a divergence that forced every caller to use
        keyword arguments to remain order-invariant, violating
        AGENTS.md's strict-typing guidelines.  Pin it here so the
        cache key + membership filter below read the same fields
        regardless of which wrapper invokes us.
        """
        cache_key = channel_id
        cached_row = self._cache_get(self._channel_cache_prefix, cache_key)

        if cached_row is None:
            row = self._db.fetch_one(
                "SELECT * FROM srv_channels WHERE id = ?", (channel_id,)
            )
            if row is None:
                return None
            self._cache_set(self._channel_cache_prefix, cache_key, dict(row))
            cached_row = dict(row)
        server_id = cached_row["server_id"]

        if not self._is_member(server_id, user_id):
            logger.warning(
                f"get_channel: user {user_id} is NOT a member of server {server_id}"
            )
            return None

        if not self.has_permission(user_id, server_id, "channels.view", channel_id):
            logger.warning(
                f"get_channel: user {user_id} does NOT have channels.view permission for channel {channel_id}"
            )
            return None

        return _row_to_channel(cached_row, self._encrypt_descriptions)

    def get_channels(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        return self.channel_handler.get_channels(user_id, server_id, channel_type)

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
        return self.channel_handler.update_channel(
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
        return self.channel_handler.delete_channel(user_id, channel_id)

    def move_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, position: int
    ) -> Channel:
        """Move a channel to a new position."""
        return self.channel_handler.move_channel(user_id, channel_id, position)

    # === Role Operations ===

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
        return self.role_handler.create_role(
            user_id, server_id, name, permissions, color, hoist, mentionable
        )

    def get_role(self, role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
        """Get a role by ID."""
        return self.role_handler.get_role(role_id, user_id)

    def get_roles(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
        """Get all roles in a server."""
        return self.role_handler.get_roles(user_id, server_id)

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
        return self.role_handler.update_role(
            user_id, role_id, name, permissions, color, hoist, mentionable
        )

    def delete_role(self, user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
        """Delete a role."""
        return self.role_handler.delete_role(user_id, role_id)

    def move_role(
        self, user_id: SnowflakeID, role_id: SnowflakeID, position: int
    ) -> Role:
        """Move a role to a new position in hierarchy."""
        return self.role_handler.move_role(user_id, role_id, position)

    # === Member Operations ===

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
    ) -> Member:
        """Add a user as a member of a server."""
        return self.member_handler.add_member(server_id, user_id, inviter_id)

    def get_member(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Member]:
        """Get a member by user ID."""
        return self.member_handler.get_member(server_id, user_id)

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
        return self.member_handler.update_member(
            user_id,
            server_id,
            member_user_id,
            nickname,
            muted,
            deafened,
            timeout_until,
            timeout_reason,
        )

    def kick_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> bool:
        """Kick a member from a server."""
        return self.member_handler.kick_member(
            user_id, server_id, member_user_id, reason
        )

    def ban_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
        delete_message_days: int = 0,
    ) -> Ban:
        """Ban a user from a server."""
        return self.member_handler.ban_member(
            user_id, server_id, member_user_id, reason
        )

    def unban_member(
        self, user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
    ) -> bool:
        """Unban a user from a server."""
        return self.member_handler.unban_member(user_id, server_id, banned_user_id)

    def get_bans(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Ban]:
        """Get all bans for a server."""
        return self.member_handler.get_bans(user_id, server_id)

    # === Role Assignment ===

    def assign_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Assign a role to a member."""
        return self.role_handler.assign_role(
            user_id, server_id, member_user_id, role_id
        )

    def remove_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Remove a role from a member."""
        return self.role_handler.remove_role(
            user_id, server_id, member_user_id, role_id
        )

    def get_member_roles(
        self, server_id: SnowflakeID, member_user_id: SnowflakeID
    ) -> List[Role]:
        """Get all roles assigned to a member."""
        return self.role_handler.get_member_roles(server_id, member_user_id)

    # === Permission Operations ===

    def get_channel_override(
        self,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> Optional[ChannelOverride]:
        """Get permission override for a channel."""
        return self.role_handler.get_channel_override(
            channel_id, target_type, target_id
        )

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
        return self.role_handler.set_channel_override(
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
        return self.role_handler.delete_channel_override(
            user_id, channel_id, target_type, target_id
        )

    # === Invite Operations ===

    def create_invite(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel."""
        return self.member_handler.create_invite(
            user_id, channel_id, max_age, max_uses, temporary
        )

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        return self.member_handler.get_invite(code)

    def use_invite(self, user_id: SnowflakeID, code: str) -> Member:
        """Use an invite to join a server."""
        return self.member_handler.use_invite(user_id, code)

    def delete_invite(self, user_id: SnowflakeID, code: str) -> bool:
        """Delete an invite."""
        return self.member_handler.delete_invite(user_id, code)
