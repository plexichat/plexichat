"""
Server models - Dataclasses for all server-related entities.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class ChannelType(Enum):
    """Types of channels."""
    TEXT = "text"
    VOICE = "voice"
    ANNOUNCEMENT = "announcement"
    STAGE = "stage"


class AuditLogAction(Enum):
    """Types of audit log actions."""
    SERVER_CREATE = "server_create"
    SERVER_UPDATE = "server_update"
    SERVER_DELETE = "server_delete"
    CHANNEL_CREATE = "channel_create"
    CHANNEL_UPDATE = "channel_update"
    CHANNEL_DELETE = "channel_delete"
    ROLE_CREATE = "role_create"
    ROLE_UPDATE = "role_update"
    ROLE_DELETE = "role_delete"
    MEMBER_JOIN = "member_join"
    MEMBER_LEAVE = "member_leave"
    MEMBER_KICK = "member_kick"
    MEMBER_BAN = "member_ban"
    MEMBER_UNBAN = "member_unban"
    MEMBER_UPDATE = "member_update"
    MEMBER_ROLE_ADD = "member_role_add"
    MEMBER_ROLE_REMOVE = "member_role_remove"
    INVITE_CREATE = "invite_create"
    INVITE_DELETE = "invite_delete"
    OVERRIDE_CREATE = "override_create"
    OVERRIDE_UPDATE = "override_update"
    OVERRIDE_DELETE = "override_delete"


class PermissionValue(Enum):
    """Permission override values."""
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class Server:
    """Represents a server (guild)."""
    id: int
    name: str
    owner_id: int
    description: Optional[str] = None
    icon_url: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0
    member_count: int = 0
    channel_count: int = 0
    role_count: int = 0
    default_role_id: Optional[int] = None
    system_channel_id: Optional[int] = None
    verification_level: int = 0
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ChannelCategory:
    """Represents a channel category for organization."""
    id: int
    server_id: int
    name: str
    position: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Channel:
    """Represents a channel within a server."""
    id: int
    server_id: int
    name: str
    channel_type: ChannelType
    category_id: Optional[int] = None
    position: int = 0
    topic: Optional[str] = None
    nsfw: bool = False
    slowmode_seconds: int = 0
    conversation_id: Optional[int] = None
    created_at: int = 0
    updated_at: int = 0
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Role:
    """Represents a role within a server."""
    id: int
    server_id: int
    name: str
    permissions: Dict[str, bool] = field(default_factory=dict)
    color: Optional[str] = None
    hoist: bool = False
    mentionable: bool = False
    position: int = 0
    is_default: bool = False
    created_at: int = 0
    updated_at: int = 0
    deleted: bool = False


@dataclass
class Member:
    """Represents a member of a server."""
    id: int
    server_id: int
    user_id: int
    nickname: Optional[str] = None
    joined_at: int = 0
    muted: bool = False
    deafened: bool = False
    inviter_id: Optional[int] = None
    roles: List[int] = field(default_factory=list)


@dataclass
class MemberRole:
    """Represents a role assignment to a member."""
    id: int
    member_id: int
    role_id: int
    assigned_at: int = 0
    assigned_by: Optional[int] = None


@dataclass
class ChannelOverride:
    """Represents a permission override for a channel."""
    id: int
    channel_id: int
    target_type: str
    target_id: int
    allow: Dict[str, bool] = field(default_factory=dict)
    deny: Dict[str, bool] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Invite:
    """Represents an invite to a server."""
    id: int
    code: str
    server_id: int
    channel_id: int
    inviter_id: int
    max_age: int = 86400
    max_uses: int = 0
    uses: int = 0
    temporary: bool = False
    created_at: int = 0
    expires_at: Optional[int] = None
    revoked: bool = False


@dataclass
class Ban:
    """Represents a ban on a server."""
    id: int
    server_id: int
    user_id: int
    banned_by: int
    reason: Optional[str] = None
    created_at: int = 0


@dataclass
class AuditLogEntry:
    """Represents an audit log entry."""
    id: int
    server_id: int
    user_id: int
    action: AuditLogAction
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    changes: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    created_at: int = 0


# Server permission definitions
SERVER_PERMISSIONS = {
    # Server management
    "server.manage": "Manage server settings",
    "server.view_audit_log": "View audit log",
    # Channel management
    "channels.manage": "Create, edit, delete channels",
    "channels.view": "View channels",
    # Role management
    "roles.manage": "Create, edit, delete roles",
    # Member management
    "members.kick": "Kick members",
    "members.ban": "Ban members",
    "members.manage_nicknames": "Change other members nicknames",
    "members.manage_roles": "Assign and remove roles",
    # Invite management
    "invites.create": "Create invites",
    "invites.manage": "Manage and delete invites",
    # Message permissions (per channel)
    "messages.send": "Send messages",
    "messages.read": "Read messages",
    "messages.manage": "Delete other members messages",
    "messages.embed_links": "Embed links",
    "messages.attach_files": "Attach files",
    "messages.mention_everyone": "Use @everyone and @here",
    "messages.add_reactions": "Add reactions",
    "messages.use_external_emojis": "Use external emojis",
    # Voice permissions
    "voice.connect": "Connect to voice channels",
    "voice.speak": "Speak in voice channels",
    "voice.mute_members": "Mute other members",
    "voice.deafen_members": "Deafen other members",
    "voice.move_members": "Move members between channels",
    # Administrator (bypasses all permissions)
    "administrator": "Full administrator access",
}

# Default permissions for @everyone role
DEFAULT_EVERYONE_PERMISSIONS = {
    "channels.view": True,
    "messages.send": True,
    "messages.read": True,
    "messages.embed_links": True,
    "messages.attach_files": True,
    "messages.add_reactions": True,
    "voice.connect": True,
    "voice.speak": True,
    "invites.create": True,
}
