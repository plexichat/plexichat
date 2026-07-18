"""Servers module - Zero-friction API for server management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import servers
    servers.setup(db, auth, messaging)

    # In any other file (use directly)
    from .composer import ServersManager
    servers_manager = ServersManager(db, auth, messaging)
    server = servers_manager.create_server(owner_id=1, name="My Server")
"""

from typing import Any, List, Optional

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

# Re-export from submodules
from .channels import (
    create_channel,
    create_category,
    get_channel,
    channel_exists,
    get_channels,
    update_channel,
    delete_channel,
    move_channel,
)
from .roles import (
    create_role,
    get_role,
    get_roles,
    update_role,
    delete_role,
    move_role,
)
from .members import (
    add_member,
    get_member,
    get_members,
    get_member_user_ids,
    get_all_shared_member_ids,
    update_member,
    remove_member,
    leave_server,
    kick_member,
    ban_member,
    unban_member,
    get_bans,
    assign_role,
    remove_role,
    get_member_roles,
)
from .invites import (
    create_invite,
    get_invite,
    get_invites,
    use_invite,
    delete_invite,
)
from .messages import (
    send_channel_message,
    get_channel_messages,
    get_audit_log,
)
from .events import (
    create_scheduled_event,
    get_scheduled_event,
    get_scheduled_events,
    update_scheduled_event,
    delete_scheduled_event,
    rsvp_event,
    remove_rsvp,
    get_event_rsvps,
    generate_recurring_instances,
)
from .templates import (
    create_template,
    get_template,
    get_template_by_id,
    get_user_templates,
    get_public_templates,
    preview_template,
    apply_template,
    delete_template,
    update_template,
)
from .onboarding import (
    set_welcome_screen,
    get_welcome_screen,
    delete_welcome_screen,
    create_onboarding_step,
    get_onboarding_step,
    get_onboarding_steps,
    update_onboarding_step,
    delete_onboarding_step,
    start_onboarding,
    complete_onboarding_step,
    get_onboarding_progress,
    reset_onboarding_progress,
)
from .permissions import (
    get_channel_override,
    set_channel_override,
    delete_channel_override,
    has_permission,
    get_permissions,
    require_permission,
)

__all__ = [
    "ServersManager",
    "setup",
    # Server operations
    "create_server",
    "get_server",
    "get_servers",
    "server_exists",
    "update_server",
    "delete_server",
    "transfer_ownership",
    # Channel operations
    "create_channel",
    "create_category",
    "get_channel",
    "channel_exists",
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
    # Permission overrides
    "get_channel_override",
    "set_channel_override",
    "delete_channel_override",
    # Permission checks
    "has_permission",
    "get_permissions",
    "require_permission",
    # Invite operations
    "create_invite",
    "get_invite",
    "get_invites",
    "use_invite",
    "delete_invite",
    # Messaging
    "send_channel_message",
    "get_channel_messages",
    "get_audit_log",
    # Scheduled events
    "create_scheduled_event",
    "get_scheduled_event",
    "get_scheduled_events",
    "update_scheduled_event",
    "delete_scheduled_event",
    "rsvp_event",
    "remove_rsvp",
    "get_event_rsvps",
    "generate_recurring_instances",
    # Templates
    "create_template",
    "get_template",
    "get_template_by_id",
    "get_user_templates",
    "get_public_templates",
    "preview_template",
    "apply_template",
    "delete_template",
    "update_template",
    # Onboarding
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
    # Models
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


# Server operations - delegate to manager
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
