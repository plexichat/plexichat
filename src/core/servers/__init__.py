"""
Server module - Zero-friction API for server management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import servers
    servers.setup(db, auth, messaging)

    # In any other file (use directly)
    from src.core import servers
    server = servers.create_server(owner_id=1, name="My Server")
"""

from typing import Optional, List, Dict, Any

from .models import (
    Server,
    Channel,
    ChannelCategory,
    Role,
    Member,
    MemberRole,
    ChannelOverride,
    Invite,
    Ban,
    AuditLogEntry,
    ChannelType,
    AuditLogAction,
    PermissionValue,
)
from .exceptions import (
    ServerError,
    ServerNotFoundError,
    ServerAccessDeniedError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelTypeError,
    CategoryNotFoundError,
    RoleNotFoundError,
    RoleAccessDeniedError,
    RoleHierarchyError,
    DefaultRoleError,
    MemberNotFoundError,
    MemberExistsError,
    InviteNotFoundError,
    InviteExpiredError,
    InviteMaxUsesError,
    BanExistsError,
    UserBannedError,
    InvalidServerNameError,
    InvalidChannelNameError,
    InvalidRoleNameError,
    PermissionDeniedError,
    OwnerCannotLeaveError,
    CannotModifyOwnerError,
)

__all__ = [
    # Models
    "Server",
    "Channel",
    "ChannelCategory",
    "Role",
    "Member",
    "MemberRole",
    "ChannelOverride",
    "Invite",
    "Ban",
    "AuditLogEntry",
    "ChannelType",
    "AuditLogAction",
    "PermissionValue",
    # Exceptions
    "ServerError",
    "ServerNotFoundError",
    "ServerAccessDeniedError",
    "ChannelNotFoundError",
    "ChannelAccessDeniedError",
    "ChannelTypeError",
    "CategoryNotFoundError",
    "RoleNotFoundError",
    "RoleAccessDeniedError",
    "RoleHierarchyError",
    "DefaultRoleError",
    "MemberNotFoundError",
    "MemberExistsError",
    "InviteNotFoundError",
    "InviteExpiredError",
    "InviteMaxUsesError",
    "BanExistsError",
    "UserBannedError",
    "InvalidServerNameError",
    "InvalidChannelNameError",
    "InvalidRoleNameError",
    "PermissionDeniedError",
    "OwnerCannotLeaveError",
    "CannotModifyOwnerError",
    # Setup
    "setup",
    # Server operations
    "create_server",
    "get_server",
    "get_servers",
    "update_server",
    "delete_server",
    "transfer_ownership",
    # Channel operations
    "create_channel",
    "create_category",
    "get_channel",
    "get_channels",
    "update_channel",
    "delete_channel",
    "move_channel",
    # Role operations
    "create_role",
    "get_role",
    "get_roles",
    "update_role",
    "delete_role",
    "move_role",
    # Member operations
    "add_member",
    "get_member",
    "get_members",
    "update_member",
    "remove_member",
    "kick_member",
    "ban_member",
    "unban_member",
    "get_bans",
    # Role assignment
    "assign_role",
    "remove_role",
    "get_member_roles",
    # Permission operations
    "get_channel_override",
    "set_channel_override",
    "delete_channel_override",
    "has_permission",
    "get_permissions",
    "require_permission",
    # Invite operations
    "create_invite",
    "get_invite",
    "get_invites",
    "use_invite",
    "delete_invite",
    # Channel messaging
    "send_channel_message",
    "get_channel_messages",
    # Audit log
    "get_audit_log",
]

_manager = None
_setup_complete = False


def setup(db, auth_module=None, messaging_module=None):
    """
    Initialize the servers module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for user verification
        messaging_module: Optional messaging module for channel messages
    """
    global _manager, _setup_complete

    from .manager import ServerManager

    _manager = ServerManager(db, auth_module, messaging_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Servers module not initialized. Call servers.setup(db) first."
        )
    return _manager


# === Server Operations ===


def create_server(
    owner_id: int,
    name: str,
    description: Optional[str] = None,
    icon_url: Optional[str] = None,
) -> Server:
    """Create a new server."""
    return _get_manager().create_server(owner_id, name, description, icon_url)


def get_server(server_id: int, user_id: int) -> Optional[Server]:
    """Get a server by ID if user has access."""
    return _get_manager().get_server(server_id, user_id)


def get_servers(user_id: int, limit: int = 100) -> List[Server]:
    """Get all servers a user is a member of."""
    return _get_manager().get_servers(user_id, limit)


def update_server(
    user_id: int,
    server_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    icon_url: Optional[str] = None,
) -> Server:
    """Update server settings."""
    return _get_manager().update_server(user_id, server_id, name, description, icon_url)


def delete_server(user_id: int, server_id: int) -> bool:
    """Delete a server (owner only)."""
    return _get_manager().delete_server(user_id, server_id)


def transfer_ownership(user_id: int, server_id: int, new_owner_id: int) -> Server:
    """Transfer server ownership to another member."""
    return _get_manager().transfer_ownership(user_id, server_id, new_owner_id)


# === Channel Operations ===


def create_channel(
    user_id: int,
    server_id: int,
    name: str,
    channel_type: ChannelType = ChannelType.TEXT,
    category_id: Optional[int] = None,
    topic: Optional[str] = None,
    nsfw: bool = False,
    slowmode_seconds: int = 0,
) -> Channel:
    """Create a new channel in a server."""
    return _get_manager().create_channel(
        user_id, server_id, name, channel_type, category_id, topic, nsfw, slowmode_seconds
    )


def create_category(
    user_id: int,
    server_id: int,
    name: str,
) -> ChannelCategory:
    """Create a new channel category."""
    return _get_manager().create_category(user_id, server_id, name)


def get_channel(channel_id: int, user_id: int) -> Optional[Channel]:
    """Get a channel by ID if user has access."""
    return _get_manager().get_channel(channel_id, user_id)


def get_channels(
    user_id: int,
    server_id: int,
    channel_type: Optional[ChannelType] = None,
) -> List[Channel]:
    """Get all channels in a server."""
    return _get_manager().get_channels(user_id, server_id, channel_type)


def update_channel(
    user_id: int,
    channel_id: int,
    name: Optional[str] = None,
    topic: Optional[str] = None,
    nsfw: Optional[bool] = None,
    slowmode_seconds: Optional[int] = None,
    category_id: Optional[int] = None,
) -> Channel:
    """Update channel settings."""
    return _get_manager().update_channel(
        user_id, channel_id, name, topic, nsfw, slowmode_seconds, category_id
    )


def delete_channel(user_id: int, channel_id: int) -> bool:
    """Delete a channel."""
    return _get_manager().delete_channel(user_id, channel_id)


def move_channel(user_id: int, channel_id: int, position: int) -> Channel:
    """Move a channel to a new position."""
    return _get_manager().move_channel(user_id, channel_id, position)


# === Role Operations ===


def create_role(
    user_id: int,
    server_id: int,
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


def get_role(role_id: int, user_id: int) -> Optional[Role]:
    """Get a role by ID."""
    return _get_manager().get_role(role_id, user_id)


def get_roles(user_id: int, server_id: int) -> List[Role]:
    """Get all roles in a server."""
    return _get_manager().get_roles(user_id, server_id)


def update_role(
    user_id: int,
    role_id: int,
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


def delete_role(user_id: int, role_id: int) -> bool:
    """Delete a role."""
    return _get_manager().delete_role(user_id, role_id)


def move_role(user_id: int, role_id: int, position: int) -> Role:
    """Move a role to a new position in hierarchy."""
    return _get_manager().move_role(user_id, role_id, position)


# === Member Operations ===


def add_member(server_id: int, user_id: int, inviter_id: Optional[int] = None) -> Member:
    """Add a user as a member of a server."""
    return _get_manager().add_member(server_id, user_id, inviter_id)


def get_member(server_id: int, user_id: int) -> Optional[Member]:
    """Get a member by user ID."""
    return _get_manager().get_member(server_id, user_id)


def get_members(
    user_id: int,
    server_id: int,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> List[Member]:
    """Get members of a server."""
    return _get_manager().get_members(user_id, server_id, limit, after_id)


def update_member(
    user_id: int,
    server_id: int,
    member_user_id: int,
    nickname: Optional[str] = None,
    muted: Optional[bool] = None,
    deafened: Optional[bool] = None,
) -> Member:
    """Update member settings."""
    return _get_manager().update_member(
        user_id, server_id, member_user_id, nickname, muted, deafened
    )


def remove_member(user_id: int, server_id: int) -> bool:
    """Remove yourself from a server (leave)."""
    return _get_manager().remove_member(user_id, server_id)


def kick_member(user_id: int, server_id: int, member_user_id: int, reason: Optional[str] = None) -> bool:
    """Kick a member from a server."""
    return _get_manager().kick_member(user_id, server_id, member_user_id, reason)


def ban_member(
    user_id: int,
    server_id: int,
    member_user_id: int,
    reason: Optional[str] = None,
    delete_message_days: int = 0,
) -> Ban:
    """Ban a user from a server."""
    return _get_manager().ban_member(user_id, server_id, member_user_id, reason, delete_message_days)


def unban_member(user_id: int, server_id: int, banned_user_id: int) -> bool:
    """Unban a user from a server."""
    return _get_manager().unban_member(user_id, server_id, banned_user_id)


def get_bans(user_id: int, server_id: int) -> List[Ban]:
    """Get all bans for a server."""
    return _get_manager().get_bans(user_id, server_id)


# === Role Assignment ===


def assign_role(user_id: int, server_id: int, member_user_id: int, role_id: int) -> bool:
    """Assign a role to a member."""
    return _get_manager().assign_role(user_id, server_id, member_user_id, role_id)


def remove_role(user_id: int, server_id: int, member_user_id: int, role_id: int) -> bool:
    """Remove a role from a member."""
    return _get_manager().remove_role(user_id, server_id, member_user_id, role_id)


def get_member_roles(server_id: int, member_user_id: int) -> List[Role]:
    """Get all roles assigned to a member."""
    return _get_manager().get_member_roles(server_id, member_user_id)


# === Permission Operations ===


def get_channel_override(
    channel_id: int,
    target_type: str,
    target_id: int,
) -> Optional[ChannelOverride]:
    """Get permission override for a channel."""
    return _get_manager().get_channel_override(channel_id, target_type, target_id)


def set_channel_override(
    user_id: int,
    channel_id: int,
    target_type: str,
    target_id: int,
    allow: Optional[Dict[str, bool]] = None,
    deny: Optional[Dict[str, bool]] = None,
) -> ChannelOverride:
    """Set permission override for a channel."""
    return _get_manager().set_channel_override(
        user_id, channel_id, target_type, target_id, allow, deny
    )


def delete_channel_override(
    user_id: int,
    channel_id: int,
    target_type: str,
    target_id: int,
) -> bool:
    """Delete a permission override."""
    return _get_manager().delete_channel_override(user_id, channel_id, target_type, target_id)


def has_permission(
    user_id: int,
    server_id: int,
    permission: str,
    channel_id: Optional[int] = None,
) -> bool:
    """Check if a user has a permission in a server/channel."""
    return _get_manager().has_permission(user_id, server_id, permission, channel_id)


def get_permissions(
    user_id: int,
    server_id: int,
    channel_id: Optional[int] = None,
) -> Dict[str, bool]:
    """Get all permissions for a user in a server/channel."""
    return _get_manager().get_permissions(user_id, server_id, channel_id)


def require_permission(
    user_id: int,
    server_id: int,
    permission: str,
    channel_id: Optional[int] = None,
) -> None:
    """Require a permission, raising if not granted."""
    return _get_manager().require_permission(user_id, server_id, permission, channel_id)


# === Invite Operations ===


def create_invite(
    user_id: int,
    channel_id: int,
    max_age: int = 86400,
    max_uses: int = 0,
    temporary: bool = False,
) -> Invite:
    """Create an invite to a channel."""
    return _get_manager().create_invite(user_id, channel_id, max_age, max_uses, temporary)


def get_invite(code: str) -> Optional[Invite]:
    """Get an invite by code."""
    return _get_manager().get_invite(code)


def get_invites(user_id: int, server_id: int) -> List[Invite]:
    """Get all invites for a server."""
    return _get_manager().get_invites(user_id, server_id)


def use_invite(user_id: int, code: str) -> Member:
    """Use an invite to join a server."""
    return _get_manager().use_invite(user_id, code)


def delete_invite(user_id: int, code: str) -> bool:
    """Delete an invite."""
    return _get_manager().delete_invite(user_id, code)


# === Channel Messaging ===


def send_channel_message(
    user_id: int,
    channel_id: int,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Send a message to a text channel."""
    return _get_manager().send_channel_message(user_id, channel_id, content, attachments)


def get_channel_messages(
    user_id: int,
    channel_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
) -> List[Any]:
    """Get messages from a text channel."""
    return _get_manager().get_channel_messages(user_id, channel_id, limit, before_id, after_id)


# === Audit Log ===


def get_audit_log(
    user_id: int,
    server_id: int,
    limit: int = 50,
    action_type: Optional[AuditLogAction] = None,
    before_id: Optional[int] = None,
) -> List[AuditLogEntry]:
    """Get audit log entries for a server."""
    return _get_manager().get_audit_log(user_id, server_id, limit, action_type, before_id)
