"""
Server models - Dataclasses for all server-related entities.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID


class ChannelType(Enum):
    """Types of channels."""

    TEXT = "text"
    VOICE = "voice"
    ANNOUNCEMENT = "announcement"
    STAGE = "stage"


class ScheduledEventType(Enum):
    """Types of scheduled events."""

    VOICE = "voice"
    STAGE = "stage"
    EXTERNAL = "external"


class ScheduledEventStatus(Enum):
    """Status of scheduled events."""

    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RSVPStatus(Enum):
    """RSVP status for events."""

    INTERESTED = "interested"
    GOING = "going"


class OnboardingStepType(Enum):
    """Types of onboarding steps."""

    SELECT_ROLES = "select_roles"
    READ_RULES = "read_rules"
    CUSTOMIZE_PROFILE = "customize_profile"
    VISIT_CHANNEL = "visit_channel"
    CUSTOM = "custom"


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
    INVITE_USE = "invite_use"
    INVITE_DELETE = "invite_delete"
    OVERRIDE_CREATE = "override_create"
    OVERRIDE_UPDATE = "override_update"
    OVERRIDE_DELETE = "override_delete"
    EVENT_CREATE = "event_create"
    EVENT_UPDATE = "event_update"
    EVENT_DELETE = "event_delete"
    TEMPLATE_CREATE = "template_create"
    TEMPLATE_DELETE = "template_delete"
    WELCOME_SCREEN_UPDATE = "welcome_screen_update"
    ONBOARDING_UPDATE = "onboarding_update"


class PermissionValue(Enum):
    """Permission override values."""

    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class Server:
    """Represents a server (guild)."""

    id: SnowflakeID
    name: str
    owner_id: SnowflakeID
    created_at: int
    updated_at: int
    description: Optional[str] = None
    icon_path: Optional[str] = None
    banner_path: Optional[str] = None
    verification_level: int = 0
    default_message_notifications: int = 0
    explicit_content_filter: int = 0
    afk_channel_id: Optional[SnowflakeID] = None
    afk_timeout: int = 300
    system_channel_id: Optional[SnowflakeID] = None
    rules_channel_id: Optional[SnowflakeID] = None
    public_updates_channel_id: Optional[SnowflakeID] = None
    preferred_locale: str = "en-US"
    features: List[str] = field(default_factory=list)
    member_count: int = 0
    max_members: int = 250000
    premium_tier: int = 0
    premium_subscription_count: int = 0
    channel_count: int = 0
    role_count: int = 0
    default_role_id: Optional[SnowflakeID] = None
    default_channel_id: Optional[SnowflakeID] = None
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

    @property
    def icon_url(self) -> Optional[str]:
        """Get the icon URL for this server."""
        return f"/api/v1/avatars/servers/{self.id}" if self.icon_path else None

    @property
    def banner_url(self) -> Optional[str]:
        """Get the banner URL for this server."""
        return f"/api/v1/avatars/banners/{self.id}" if self.banner_path else None

    def to_dict(self) -> Dict[str, Any]:
        """Convert server to dictionary including properties."""
        return {
            "id": str(self.id),
            "name": self.name,
            "owner_id": str(self.owner_id),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "icon_url": self.icon_url,
            "banner_url": self.banner_url,
            "verification_level": self.verification_level,
            "member_count": self.member_count,
            "features": self.features,
            "premium_tier": self.premium_tier,
            "default_channel_id": str(self.default_channel_id) if self.default_channel_id else None,
        }


@dataclass
class Channel:
    """Represents a channel within a server."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    channel_type: ChannelType
    created_at: int
    updated_at: int
    position: int = 0
    topic: Optional[str] = None
    nsfw: bool = False
    last_message_id: Optional[SnowflakeID] = None
    rate_limit_per_user: int = 0
    parent_id: Optional[SnowflakeID] = None
    category_id: Optional[SnowflakeID] = None
    permissions_locked: bool = True
    user_limit: int = 0
    bitrate: int = 64000
    rtc_region: Optional[str] = None
    slowmode_seconds: int = 0
    read_receipts_enabled: bool = True
    conversation_id: Optional[SnowflakeID] = None
    deleted: bool = False
    deleted_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ChannelCategory:
    """Represents a channel category within a server."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    created_at: int
    updated_at: int
    position: int = 0


@dataclass
class Role:
    """Represents a server role."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    created_at: int
    updated_at: int
    color: int = 0
    hoist: bool = False
    position: int = 0
    permissions: Dict[str, bool] = field(default_factory=dict)
    managed: bool = False
    mentionable: bool = False
    is_default: bool = False
    deleted: bool = False


@dataclass
class Member:
    """Represents a member of a server."""

    id: SnowflakeID
    server_id: SnowflakeID
    user_id: SnowflakeID
    joined_at: int
    updated_at: int
    nickname: Optional[str] = None
    username: Optional[str] = None  # Populated via enrichment
    avatar_url: Optional[str] = None  # Populated via enrichment
    roles: List[SnowflakeID] = field(default_factory=list)
    deaf: bool = False
    mute: bool = False
    pending: bool = False
    premium_since: Optional[int] = None
    permissions: Optional[Dict[str, bool]] = None
    muted: bool = False
    deafened: bool = False
    inviter_id: Optional[SnowflakeID] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert member to dictionary including properties."""
        return {
            "id": str(self.id),
            "server_id": str(self.server_id),
            "user_id": str(self.user_id),
            "joined_at": self.joined_at,
            "updated_at": self.updated_at,
            "nickname": self.nickname,
            "roles": [str(r) for r in self.roles],
            "deaf": self.deaf,
            "mute": self.mute,
            "muted": self.muted,
            "deafened": self.deafened,
            "avatar_url": self.avatar_url,
        }


@dataclass
class ChannelOverride:
    """Represents a permission override for a channel."""

    id: SnowflakeID
    channel_id: SnowflakeID
    target_id: SnowflakeID  # Either user_id or role_id
    target_type: str  # "user" or "role"
    allow: Dict[str, bool] = field(default_factory=dict)
    deny: Dict[str, bool] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Invite:
    """Represents a server invite."""

    code: str
    server_id: SnowflakeID
    channel_id: SnowflakeID
    inviter_id: SnowflakeID
    created_at: int
    id: Optional[SnowflakeID] = None
    expires_at: Optional[int] = None
    max_age: int = 0
    max_uses: int = 0
    uses: int = 0
    temporary: bool = False
    revoked: bool = False


@dataclass
class Ban:
    """Represents a server ban."""

    server_id: SnowflakeID
    user_id: SnowflakeID
    created_at: int
    id: Optional[SnowflakeID] = None
    reason: Optional[str] = None
    banned_by: Optional[SnowflakeID] = None


@dataclass
class AuditLogEntry:
    """Represents an entry in the server audit log."""

    id: SnowflakeID
    server_id: SnowflakeID
    user_id: SnowflakeID
    action_type: AuditLogAction
    created_at: int
    target_type: Optional[str] = None
    target_id: Optional[SnowflakeID] = None
    reason: Optional[str] = None
    changes: Optional[Dict[str, Any]] = None


@dataclass
class ScheduledEvent:
    """Represents a scheduled server event."""

    id: int
    server_id: int
    creator_id: int
    name: str
    start_time: int
    created_at: int
    updated_at: int
    description: Optional[str] = None
    event_type: ScheduledEventType = ScheduledEventType.VOICE
    channel_id: Optional[int] = None
    location: Optional[str] = None
    end_time: Optional[int] = None
    timezone: str = "UTC"
    status: ScheduledEventStatus = ScheduledEventStatus.SCHEDULED
    image_url: Optional[str] = None
    interested_count: int = 0
    going_count: int = 0
    rrule: Optional[str] = None
    parent_event_id: Optional[int] = None


@dataclass
class EventRSVP:
    """Represents an RSVP to a scheduled event."""

    id: int
    event_id: int
    user_id: int
    status: RSVPStatus
    created_at: int
    updated_at: int


@dataclass
class ServerTemplate:
    """Represents a server template."""

    id: int
    name: str
    creator_id: int
    code: str
    created_at: int
    updated_at: int
    description: Optional[str] = None
    source_server_id: Optional[int] = None
    usage_count: int = 0
    is_public: bool = False


@dataclass
class TemplateData:
    """Represents the data snapshot of a template."""

    id: int
    template_id: int
    created_at: int
    channels: List[Dict[str, Any]] = field(default_factory=list)
    categories: List[Dict[str, Any]] = field(default_factory=list)
    roles: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class WelcomeScreen:
    """Represents a server welcome screen."""

    id: int
    server_id: int
    created_at: int
    updated_at: int
    description: Optional[str] = None
    enabled: bool = True
    welcome_channels: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class OnboardingStep:
    """Represents an onboarding step."""

    id: int
    server_id: int
    step_type: OnboardingStepType
    title: str
    created_at: int
    updated_at: int
    description: Optional[str] = None
    position: int = 0
    required: bool = False
    options: Optional[Dict[str, Any]] = None


@dataclass
class OnboardingProgress:
    """Represents a user's onboarding progress."""

    id: int
    server_id: int
    user_id: int
    started_at: int
    completed_steps: List[int] = field(default_factory=list)
    completed: bool = False
    completed_at: Optional[int] = None


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
    # Emoji management
    "emojis.manage": "Create, edit, delete custom emojis",
    # Message permissions (per channel)
    "messages.send": "Send messages",
    "messages.read": "Read messages",
    "messages.manage": "Delete other members messages",
    "messages.embed_links": "Embed links",
    "messages.attach_files": "Attach files",
    "messages.mention_everyone": "Use @everyone and @here",
    "messages.add_reactions": "Add reactions",
    "messages.use_external_emojis": "Use external emojis",
    "messages.bypass_slowmode": "Bypass channel slowmode",
    # Voice permissions
    "voice.connect": "Connect to voice channels",
    "voice.speak": "Speak in voice channels",
    "voice.mute_members": "Mute other members",
    "voice.deafen_members": "Deafen other members",
    "voice.move_members": "Move members between channels",
    # Event permissions
    "events.manage": "Create, edit, delete scheduled events",
    "events.view": "View scheduled events",
    # Template permissions
    "templates.manage": "Create and manage server templates",
    # Onboarding permissions
    "onboarding.manage": "Manage welcome screen and onboarding",
    # AutoMod permissions
    "server.automod": "Manage server auto-moderation rules",
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
    "events.view": True,
}
