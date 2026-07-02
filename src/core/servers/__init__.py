"""Servers module - Zero-friction API for server management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import servers
    servers.setup(db, auth, messaging)

    # In any other file (use directly)
    from src.core.servers import ServersManager
    servers_manager = ServersManager(db, auth, messaging)
    server = servers_manager.create_server(owner_id=1, name="My Server")
"""

from typing import Any, Dict, List, Optional

from .composer import ServersManager
from .models import (
    Server,
    Channel,
    ChannelCategory,
    Role,
    Member,
    ChannelOverride,
    Invite,
    Ban,
    AuditLogEntry,
    ChannelType,
    AuditLogAction,
    PermissionValue,
    ScheduledEvent,
    EventRSVP,
    ScheduledEventType,
    ScheduledEventStatus,
    RSVPStatus,
    ServerTemplate,
    TemplateData,
    WelcomeScreen,
    OnboardingStep,
    OnboardingProgress,
    OnboardingStepType,
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
    BanNotFoundError,
    OwnerCannotLeaveError,
    CannotModifyOwnerError,
    ScheduledEventNotFoundError,
    ScheduledEventError,
    InvalidEventTimeError,
    TemplateNotFoundError,
    TemplateError,
    InvalidTemplateCodeError,
    WelcomeScreenNotFoundError,
    OnboardingStepNotFoundError,
    OnboardingError,
)

__all__ = [
    "ServersManager",
    "setup",
    "create_server",
    "get_server",
    "get_servers",
    "server_exists",
    "update_server",
    "delete_server",
    "transfer_ownership",
    "create_channel",
    "create_category",
    "get_channel",
    "get_channels",
    "update_channel",
    "delete_channel",
    "move_channel",
    "create_role",
    "get_role",
    "get_roles",
    "update_role",
    "delete_role",
    "move_role",
    "add_member",
    "get_member",
    "get_members",
    "get_member_user_ids",
    "get_all_shared_member_ids",
    "update_member",
    "remove_member",
    "leave_server",
    "kick_member",
    "ban_member",
    "unban_member",
    "get_bans",
    "assign_role",
    "remove_role",
    "get_member_roles",
    "get_channel_override",
    "set_channel_override",
    "delete_channel_override",
    "has_permission",
    "get_permissions",
    "require_permission",
    "create_invite",
    "get_invite",
    "get_invites",
    "use_invite",
    "delete_invite",
    "send_channel_message",
    "get_channel_messages",
    "get_audit_log",
    "create_scheduled_event",
    "get_scheduled_event",
    "get_scheduled_events",
    "update_scheduled_event",
    "delete_scheduled_event",
    "rsvp_event",
    "remove_rsvp",
    "get_event_rsvps",
    "generate_recurring_instances",
    "create_template",
    "get_template",
    "get_template_by_id",
    "get_user_templates",
    "get_public_templates",
    "preview_template",
    "apply_template",
    "delete_template",
    "update_template",
    "set_welcome_screen",
    "get_welcome_screen",
    "delete_welcome_screen",
    "create_onboarding_step",
    "get_onboarding_step",
    "get_onboarding_steps",
    "update_onboarding_step",
    "delete_onboarding_step",
    "start_onboarding",
    "complete_onboarding_step",
    "get_onboarding_progress",
    "reset_onboarding_progress",
    "Server",
    "Channel",
    "ChannelCategory",
    "Role",
    "Member",
    "ChannelOverride",
    "Invite",
    "Ban",
    "AuditLogEntry",
    "ChannelType",
    "AuditLogAction",
    "PermissionValue",
    "ScheduledEvent",
    "EventRSVP",
    "ScheduledEventType",
    "ScheduledEventStatus",
    "RSVPStatus",
    "ServerTemplate",
    "TemplateData",
    "WelcomeScreen",
    "OnboardingStep",
    "OnboardingProgress",
    "OnboardingStepType",
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
    "BanNotFoundError",
    "OwnerCannotLeaveError",
    "CannotModifyOwnerError",
    "ScheduledEventNotFoundError",
    "ScheduledEventError",
    "InvalidEventTimeError",
    "TemplateNotFoundError",
    "TemplateError",
    "InvalidTemplateCodeError",
    "WelcomeScreenNotFoundError",
    "OnboardingStepNotFoundError",
    "OnboardingError",
]

_manager: Optional[ServersManager] = None
_setup_complete = False


def setup(
    db: Any,
    auth_module: Optional[Any] = None,
    messaging_module: Optional[Any] = None,
    notifications_module: Optional[Any] = None,
    events_module: Optional[Any] = None,
) -> None:
    """Initialize the servers module."""
    global _manager, _setup_complete

    _manager = ServersManager(
        db, auth_module, messaging_module, notifications_module, events_module
    )
    _setup_complete = True


def _get_manager() -> ServersManager:
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Servers module not initialized. Call servers.setup(db) first."
        )
    return _manager


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


def server_exists(server_id: int) -> bool:
    """Check if a server exists by ID (without permission check)."""
    return _get_manager().server_exists(server_id)


def update_server(
    user_id: int,
    server_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    icon_url: Optional[str] = None,
    default_channel_id: Optional[int] = None,
) -> Server:
    """Update server settings."""
    return _get_manager().update_server(
        user_id, server_id, name, description, icon_url, default_channel_id
    )


def delete_server(user_id: int, server_id: int) -> bool:
    """Delete a server (owner only)."""
    return _get_manager().delete_server(user_id, server_id)


def transfer_ownership(user_id: int, server_id: int, new_owner_id: int) -> Server:
    """Transfer server ownership to another member."""
    return _get_manager().transfer_ownership(user_id, server_id, new_owner_id)


def create_channel(
    user_id: int,
    server_id: int,
    name: str,
    channel_type: ChannelType = ChannelType.TEXT,
    category_id: Optional[int] = None,
    topic: Optional[str] = None,
    nsfw: bool = False,
    slowmode_seconds: int = 0,
    read_receipts_enabled: bool = True,
) -> Channel:
    """Create a new channel in a server."""
    return _get_manager().create_channel(
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
    user_id: int,
    server_id: int,
    name: str,
) -> ChannelCategory:
    """Create a new channel category."""
    return _get_manager().create_category(user_id, server_id, name)


def get_channel(channel_id: int, user_id: int) -> Optional[Channel]:
    """Get a channel by ID if user has access."""
    return _get_manager().get_channel(channel_id, user_id)


def channel_exists(channel_id: int) -> bool:
    """Membership-agnostic existence probe.

    Returns ``True`` if the channel row exists at all, regardless
    of whether the caller is a member or has any permission.
    Use this to distinguish ``404`` (channel gone) from ``403``
    (exists, caller blocked) in the channels API: pre-check
    ``channel_exists`` first; only treat a non-existent channel
    as ``404``; treat a missing ``get_channel`` result for an
    existing channel as ``403``. Single ``SELECT 1`` query — cheap
    enough to call on every PATCH / invite request without load
    concerns.
    """
    return _get_manager().channel_exists(channel_id)  # type: ignore[attr-defined]  # mixed-in via ServerManager extends ChannelOpsMixin


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
    read_receipts_enabled: Optional[bool] = None,
    category_id: Optional[int] = None,
) -> Channel:
    """Update channel settings."""
    return _get_manager().update_channel(
        user_id,
        channel_id,
        name,
        topic,
        nsfw,
        slowmode_seconds,
        read_receipts_enabled,
        category_id,
    )


def delete_channel(user_id: int, channel_id: int) -> bool:
    """Delete a channel."""
    return _get_manager().delete_channel(user_id, channel_id)


def move_channel(user_id: int, channel_id: int, position: int) -> Channel:
    """Move a channel to a new position."""
    return _get_manager().move_channel(user_id, channel_id, position)


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


def add_member(
    server_id: int, user_id: int, inviter_id: Optional[int] = None
) -> Member:
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


def get_member_user_ids(
    server_id: int,
    exclude_user_id: Optional[int] = None,
) -> List[int]:
    """Get just the user IDs of server members."""
    return _get_manager().get_member_user_ids(server_id, exclude_user_id)


def get_all_shared_member_ids(user_id: int) -> List[int]:
    """Get IDs of all users who share at least one server with the given user."""
    return _get_manager().get_all_shared_member_ids(user_id)


def update_member(
    user_id: int,
    server_id: int,
    member_user_id: int,
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


def remove_member(user_id: int, server_id: int) -> bool:
    """Remove yourself from a server (leave)."""
    return _get_manager().remove_member(user_id, server_id)


def leave_server(user_id: int, server_id: int) -> bool:
    """Alias for remove_member - leave a server."""
    return _get_manager().leave_server(user_id, server_id)


def kick_member(
    user_id: int, server_id: int, member_user_id: int, reason: Optional[str] = None
) -> bool:
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
    return _get_manager().ban_member(
        user_id, server_id, member_user_id, reason, delete_message_days
    )


def unban_member(user_id: int, server_id: int, banned_user_id: int) -> bool:
    """Unban a user from a server."""
    return _get_manager().unban_member(user_id, server_id, banned_user_id)


def get_bans(user_id: int, server_id: int) -> List[Ban]:
    """Get all bans for a server."""
    return _get_manager().get_bans(user_id, server_id)


def assign_role(
    user_id: int, server_id: int, member_user_id: int, role_id: int
) -> bool:
    """Assign a role to a member."""
    return _get_manager().assign_role(user_id, server_id, member_user_id, role_id)


def remove_role(
    user_id: int, server_id: int, member_user_id: int, role_id: int
) -> bool:
    """Remove a role from a member."""
    return _get_manager().remove_role(user_id, server_id, member_user_id, role_id)


def get_member_roles(server_id: int, member_user_id: int) -> List[Role]:
    """Get all roles assigned to a member."""
    return _get_manager().get_member_roles(server_id, member_user_id)


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
    return _get_manager().delete_channel_override(
        user_id, channel_id, target_type, target_id
    )


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


def create_invite(
    user_id: int,
    channel_id: int,
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


def get_invites(user_id: int, server_id: int) -> List[Invite]:
    """Get all invites for a server."""
    return _get_manager().get_invites(user_id, server_id)


def use_invite(user_id: int, code: str) -> Member:
    """Use an invite to join a server."""
    return _get_manager().use_invite(user_id, code)


def delete_invite(user_id: int, code: str) -> bool:
    """Delete an invite."""
    return _get_manager().delete_invite(user_id, code)


def send_channel_message(
    user_id: int,
    channel_id: int,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    reply_to_id: Optional[int] = None,
) -> Any:
    """Send a message to a text channel."""
    return _get_manager().send_channel_message(
        user_id, channel_id, content, attachments, reply_to_id
    )


def get_channel_messages(
    user_id: int,
    channel_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
) -> List[Any]:
    """Get messages from a text channel."""
    return _get_manager().get_channel_messages(
        user_id, channel_id, limit, before_id, after_id
    )


def get_audit_log(
    user_id: int,
    server_id: int,
    limit: int = 50,
    action_type: Optional[AuditLogAction] = None,
    before_id: Optional[int] = None,
) -> List[AuditLogEntry]:
    """Get audit log entries for a server."""
    return _get_manager().get_audit_log(
        user_id, server_id, limit, action_type, before_id
    )


def create_scheduled_event(
    user_id: int,
    server_id: int,
    name: str,
    start_time: int,
    event_type: ScheduledEventType = ScheduledEventType.VOICE,
    description: Optional[str] = None,
    channel_id: Optional[int] = None,
    location: Optional[str] = None,
    end_time: Optional[int] = None,
    timezone_str: str = "UTC",
    image_url: Optional[str] = None,
    rrule: Optional[str] = None,
) -> ScheduledEvent:
    """Create a new scheduled event."""
    return _get_manager().create_scheduled_event(
        user_id,
        server_id,
        name,
        start_time,
        event_type,
        description,
        channel_id,
        location,
        end_time,
        timezone_str,
        image_url,
        rrule,
    )


def get_scheduled_event(event_id: int, user_id: int) -> Optional[ScheduledEvent]:
    """Get a scheduled event by ID."""
    return _get_manager().get_scheduled_event(event_id, user_id)


def get_scheduled_events(
    user_id: int,
    server_id: int,
    status: Optional[ScheduledEventStatus] = None,
    limit: int = 50,
) -> List[ScheduledEvent]:
    """Get scheduled events for a server."""
    return _get_manager().get_scheduled_events(user_id, server_id, status, limit)


def update_scheduled_event(
    user_id: int,
    event_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    channel_id: Optional[int] = None,
    location: Optional[str] = None,
    image_url: Optional[str] = None,
    status: Optional[ScheduledEventStatus] = None,
) -> ScheduledEvent:
    """Update a scheduled event."""
    return _get_manager().update_scheduled_event(
        user_id,
        event_id,
        name,
        description,
        start_time,
        end_time,
        channel_id,
        location,
        image_url,
        status,
    )


def delete_scheduled_event(user_id: int, event_id: int) -> bool:
    """Delete a scheduled event."""
    return _get_manager().delete_scheduled_event(user_id, event_id)


def rsvp_event(user_id: int, event_id: int, status: RSVPStatus) -> EventRSVP:
    """RSVP to an event."""
    return _get_manager().rsvp_event(user_id, event_id, status)


def remove_rsvp(user_id: int, event_id: int) -> bool:
    """Remove RSVP from an event."""
    return _get_manager().remove_rsvp(user_id, event_id)


def get_event_rsvps(
    user_id: int,
    event_id: int,
    status: Optional[RSVPStatus] = None,
    limit: int = 100,
) -> List[EventRSVP]:
    """Get RSVPs for an event."""
    return _get_manager().get_event_rsvps(user_id, event_id, status, limit)


def generate_recurring_instances(
    event_id: int, user_id: int, count: int = 10
) -> List[ScheduledEvent]:
    """Generate instances of a recurring event."""
    return _get_manager().generate_recurring_instances(event_id, user_id, count)


def create_template(
    user_id: int,
    server_id: int,
    name: str,
    description: Optional[str] = None,
) -> ServerTemplate:
    """Create a template from an existing server."""
    return _get_manager().create_template(user_id, server_id, name, description)


def get_template(code: str, user_id: Optional[int] = None) -> Optional[ServerTemplate]:
    """Get a template by code."""
    return _get_manager().get_template(code, user_id)


def get_template_by_id(template_id: int, user_id: int) -> Optional[ServerTemplate]:
    """Get a template by ID."""
    return _get_manager().get_template_by_id(template_id, user_id)


def get_user_templates(user_id: int, limit: int = 50) -> List[ServerTemplate]:
    """Get templates created by a user."""
    return _get_manager().get_user_templates(user_id, limit)


def get_public_templates(limit: int = 50) -> List[ServerTemplate]:
    """Get public templates."""
    return _get_manager().get_public_templates(limit)


def preview_template(code: str) -> Optional[TemplateData]:
    """Preview template data without applying."""
    return _get_manager().preview_template(code)


def apply_template(
    user_id: int,
    code: str,
    server_name: str,
    server_description: Optional[str] = None,
) -> Optional[Server]:
    """Apply a template to create a new server."""
    return _get_manager().apply_template(user_id, code, server_name, server_description)


def delete_template(user_id: int, code: str) -> bool:
    """Delete a template."""
    return _get_manager().delete_template(user_id, code)


def update_template(
    user_id: int,
    code: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_public: Optional[bool] = None,
) -> ServerTemplate:
    """Update template metadata."""
    return _get_manager().update_template(user_id, code, name, description, is_public)


def set_welcome_screen(
    user_id: int,
    server_id: int,
    description: Optional[str] = None,
    welcome_channels: Optional[List[Dict[str, Any]]] = None,
    enabled: bool = True,
) -> WelcomeScreen:
    """Set or update the welcome screen for a server."""
    return _get_manager().set_welcome_screen(
        user_id, server_id, description, welcome_channels, enabled
    )


def get_welcome_screen(server_id: int, user_id: int) -> Optional[WelcomeScreen]:
    """Get the welcome screen for a server."""
    return _get_manager().get_welcome_screen(server_id, user_id)


def delete_welcome_screen(user_id: int, server_id: int) -> bool:
    """Delete the welcome screen for a server."""
    return _get_manager().delete_welcome_screen(user_id, server_id)


def create_onboarding_step(
    user_id: int,
    server_id: int,
    step_type: OnboardingStepType,
    title: str,
    description: Optional[str] = None,
    required: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> OnboardingStep:
    """Create an onboarding step."""
    return _get_manager().create_onboarding_step(
        user_id, server_id, step_type, title, description, required, options
    )


def get_onboarding_step(step_id: int, user_id: int) -> Optional[OnboardingStep]:
    """Get an onboarding step by ID."""
    return _get_manager().get_onboarding_step(step_id, user_id)


def get_onboarding_steps(user_id: int, server_id: int) -> List[OnboardingStep]:
    """Get all onboarding steps for a server."""
    return _get_manager().get_onboarding_steps(user_id, server_id)


def update_onboarding_step(
    user_id: int,
    step_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    required: Optional[bool] = None,
    options: Optional[Dict[str, Any]] = None,
    position: Optional[int] = None,
) -> OnboardingStep:
    """Update an onboarding step."""
    return _get_manager().update_onboarding_step(
        user_id, step_id, title, description, required, options, position
    )


def delete_onboarding_step(user_id: int, step_id: int) -> bool:
    """Delete an onboarding step."""
    return _get_manager().delete_onboarding_step(user_id, step_id)


def start_onboarding(user_id: int, server_id: int) -> OnboardingProgress:
    """Start onboarding for a user."""
    return _get_manager().start_onboarding(user_id, server_id)


def complete_onboarding_step(
    user_id: int,
    server_id: int,
    step_id: int,
    response: Optional[Dict[str, Any]] = None,
) -> OnboardingProgress:
    """Mark an onboarding step as complete."""
    return _get_manager().complete_onboarding_step(
        user_id, server_id, step_id, response
    )


def get_onboarding_progress(
    user_id: int, server_id: int
) -> Optional[OnboardingProgress]:
    """Get onboarding progress for a user."""
    return _get_manager().get_onboarding_progress(user_id, server_id)


def reset_onboarding_progress(user_id: int, server_id: int) -> bool:
    """Reset onboarding progress for a user."""
    return _get_manager().reset_onboarding_progress(user_id, server_id)
