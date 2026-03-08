"""
Server schemas - Request/response models for server/guild endpoints.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID
from .users import UserPublicResponse


class ServerCreateRequest(BaseModel):
    """Server creation request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=2, max_length=100, description="Server name")
    description: Optional[str] = Field(
        None, max_length=1000, description="Server description"
    )
    icon_url: Optional[str] = Field(None, description="Server icon URL")


class ServerUpdateRequest(BaseModel):
    """Server update request."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(
        None, min_length=2, max_length=100, description="Server name"
    )
    description: Optional[str] = Field(
        None, max_length=1000, description="Server description"
    )
    icon_url: Optional[str] = Field(None, description="Server icon URL")
    default_channel_id: Optional[SnowflakeID] = Field(
        None, description="Default channel ID to select when joining"
    )


class ServerResponse(BaseModel):
    """Server information response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Server name")
    description: Optional[str] = Field(None, description="Server description")
    icon_url: Optional[str] = Field(None, description="Server icon URL")
    owner_id: SnowflakeID = Field(..., description="Owner user ID")
    member_count: int = Field(0, description="Number of members")
    default_channel_id: Optional[SnowflakeID] = Field(
        None, description="Default channel ID"
    )
    verification_level: int = Field(
        0, description="Verification level required to join"
    )
    default_message_notifications: int = Field(
        0, description="Default notification level"
    )
    created_at: int = Field(..., description="Creation timestamp")


class ChannelCreateRequest(BaseModel):
    """Channel creation request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100, description="Channel name")
    channel_type: Optional[str] = Field(
        "text", description="Channel type: text, voice, category"
    )
    topic: Optional[str] = Field(None, max_length=1024, description="Channel topic")
    category_id: Optional[SnowflakeID] = Field(None, description="Parent category ID")
    nsfw: bool = Field(False, description="NSFW flag")
    slowmode_seconds: int = Field(
        0, ge=0, le=21600, description="Slowmode delay in seconds"
    )
    read_receipts_enabled: bool = Field(
        True, description="Whether to track read receipts in this channel"
    )


class ChannelResponse(BaseModel):
    """Channel information response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Channel ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Channel name")
    channel_type: str = Field(..., description="Channel type: text, voice, category")
    topic: Optional[str] = Field(None, description="Channel topic")
    position: int = Field(0, description="Channel position")
    category_id: Optional[SnowflakeID] = Field(None, description="Parent category ID")
    nsfw: bool = Field(False, description="NSFW flag")
    slowmode_seconds: int = Field(0, description="Slowmode delay in seconds")
    read_receipts_enabled: bool = Field(
        True, description="Whether read receipts are enabled"
    )
    created_at: int = Field(..., description="Creation timestamp")

    # Optional fields for DM support
    recipient_id: Optional[SnowflakeID] = Field(
        None, description="Recipient user ID (DMs only)"
    )
    recipient: Optional[UserPublicResponse] = Field(
        None, description="Recipient details (DMs only)"
    )


class ChannelUpdateRequest(BaseModel):
    """Channel update request."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="Channel name"
    )
    topic: Optional[str] = Field(None, max_length=1024, description="Channel topic")
    position: Optional[int] = Field(None, ge=0, description="Channel position")
    nsfw: Optional[bool] = Field(None, description="NSFW flag")
    slowmode_seconds: Optional[int] = Field(
        None, ge=0, le=21600, description="Slowmode delay"
    )
    read_receipts_enabled: Optional[bool] = Field(
        None, description="Whether to track read receipts"
    )


class PresenceResponse(BaseModel):
    """Presence information."""

    model_config = ConfigDict(from_attributes=True)
    status: str = Field(
        "offline", description="User status: online, idle, dnd, offline"
    )


class MemberResponse(BaseModel):
    """Server member information response."""

    model_config = ConfigDict(from_attributes=True)
    member_id: Optional[SnowflakeID] = Field(None, description="Member record ID")
    user_id: SnowflakeID = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    nickname: Optional[str] = Field(None, description="Server-specific nickname")
    avatar_url: Optional[str] = Field(None, description="User avatar URL")
    joined_at: Optional[int] = Field(None, description="Join timestamp")
    roles: List[SnowflakeID] = Field(
        default_factory=list, description="List of role IDs"
    )
    presence: PresenceResponse = Field(
        default_factory=lambda: PresenceResponse(status="offline")
    )
    badges: List[str] = Field(default_factory=list, description="User badges")
    timeout_until: Optional[int] = Field(
        None, description="Timestamp until user is timed out"
    )
    timeout_reason: Optional[str] = Field(None, description="Reason for timeout")


class RoleResponse(BaseModel):
    """Role information response."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Role ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Role name")
    color: Optional[str] = Field(default=None, description="Role color hex")
    position: int = Field(default=0, description="Role position")
    permissions: Dict[str, Any] = Field(
        default_factory=dict, description="Role permissions"
    )
    hoist: bool = Field(default=False, description="Display separately in member list")
    mentionable: bool = Field(default=False, description="Can be mentioned")
    is_default: bool = Field(
        default=False, description="Whether this is the @everyone role"
    )


class RoleCreateRequest(BaseModel):
    """Role creation request."""

    model_config = ConfigDict(from_attributes=True)
    name: str = Field(..., min_length=1, max_length=100, description="Role name")
    color: Optional[str] = Field(
        None, pattern=r"^#[0-9A-Fa-f]{6}$", description="Role color hex"
    )
    permissions: Dict[str, Any] = Field(
        default_factory=dict, description="Role permissions"
    )
    hoist: bool = Field(False, description="Display separately in member list")
    mentionable: bool = Field(False, description="Can be mentioned")


class RoleUpdateRequest(BaseModel):
    """Role update request."""

    model_config = ConfigDict(from_attributes=True)
    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="Role name"
    )
    color: Optional[str] = Field(
        None, pattern=r"^#[0-9A-Fa-f]{6}$", description="Role color hex"
    )
    permissions: Optional[Dict[str, Any]] = Field(None, description="Role permissions")
    hoist: Optional[bool] = Field(None, description="Display separately in member list")
    mentionable: Optional[bool] = Field(None, description="Can be mentioned")
    position: Optional[int] = Field(None, ge=0, description="Role position")


class BanResponse(BaseModel):
    """Server ban information response."""

    model_config = ConfigDict(from_attributes=True)
    user_id: SnowflakeID = Field(..., description="Banned user ID")
    reason: Optional[str] = Field(None, description="Reason for ban")
    banned_by: Optional[SnowflakeID] = Field(
        None, description="User who performed the ban"
    )
    banned_at: Optional[int] = Field(None, description="Ban timestamp")


class BanCreateRequest(BaseModel):
    """Ban creation request."""

    model_config = ConfigDict(from_attributes=True)
    reason: Optional[str] = Field(None, max_length=512, description="Reason for ban")
    delete_message_days: int = Field(
        0, ge=0, le=7, description="Number of days of messages to delete"
    )


class AuditLogEntryResponse(BaseModel):
    """Audit log entry response."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Entry ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    user_id: SnowflakeID = Field(..., description="User who performed action")
    user: Optional[UserPublicResponse] = Field(
        default=None, description="User information"
    )
    action: str = Field(..., description="Action type")
    target_type: Optional[str] = Field(default=None, description="Target object type")
    target_id: Optional[SnowflakeID] = Field(
        default=None, description="Target object ID"
    )
    target_name: Optional[str] = Field(default=None, description="Target object name")
    changes: Optional[Dict[str, Any]] = Field(
        default=None, description="Dictionary of changes"
    )
    reason: Optional[str] = Field(default=None, description="Reason for action")
    created_at: Optional[int] = Field(default=None, description="Creation timestamp")


class WebhookResponse(BaseModel):
    """Webhook information response."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Webhook ID")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    creator_id: Optional[SnowflakeID] = Field(None, description="Creator user ID")
    name: str = Field(..., description="Webhook name")
    avatar_url: Optional[str] = Field(None, description="Webhook avatar URL")
    created_at: int = Field(..., description="Creation timestamp")


class InviteResponse(BaseModel):
    """Server invite response."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="Invite code")
    server_id: SnowflakeID = Field(..., description="Server ID")
    channel_id: Optional[SnowflakeID] = Field(default=None, description="Channel ID")
    inviter_id: Optional[SnowflakeID] = Field(
        default=None, description="Inviter user ID"
    )
    uses: int = Field(default=0, description="Number of uses")
    max_uses: int = Field(default=0, description="Maximum uses (0 = unlimited)")
    max_age: int = Field(
        default=0, description="Max age in seconds (0 = never expires)"
    )
    temporary: bool = Field(default=False, description="Temporary membership")
    created_at: Optional[int] = Field(default=None, description="Creation timestamp")
    expires_at: Optional[int] = Field(default=None, description="Expiration timestamp")


# ==================== Automod Schemas ====================


class AutomodRuleAction(BaseModel):
    """Automod rule action schema."""

    model_config = ConfigDict(from_attributes=True)

    action_type: str = Field(
        ..., description="Action type (delete_message, timeout_user, etc.)"
    )
    duration_seconds: Optional[int] = Field(
        None, description="Duration for timeout/ban"
    )
    reason: Optional[str] = Field(None, max_length=512, description="Reason for action")
    notify_user: bool = Field(True, description="Whether to notify the user")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class AutomodRuleResponse(BaseModel):
    """Automod rule response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Rule ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Rule name")
    rule_type: str = Field(..., description="Rule type")
    enabled: bool = Field(..., description="Whether rule is enabled")
    config: Dict[str, Any] = Field(..., description="Rule configuration")
    actions: List[AutomodRuleAction] = Field(..., description="Actions to take")
    applied_roles: List[SnowflakeID] = Field(
        default_factory=list,
        description="Role IDs this rule applies to (empty = everyone)",
    )
    exempt_roles: List[SnowflakeID] = Field(
        default_factory=list, description="Exempt role IDs"
    )
    exempt_channels: List[SnowflakeID] = Field(
        default_factory=list, description="Exempt channel IDs"
    )
    priority: int = Field(0, description="Rule priority")
    check_all: bool = Field(False, description="Whether to check all rules after match")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")
    created_by: SnowflakeID = Field(..., description="User ID who created the rule")


class AutomodRuleCreateRequest(BaseModel):
    """Automod rule creation request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100)
    rule_type: str = Field(..., description="Rule type")
    config: Dict[str, Any] = Field(..., description="Rule configuration")
    actions: List[AutomodRuleAction] = Field(..., description="Actions to take")
    applied_roles: Optional[List[SnowflakeID]] = Field(
        None, description="Role IDs this rule applies to"
    )
    exempt_roles: Optional[List[SnowflakeID]] = Field(
        None, description="Exempt role IDs"
    )
    exempt_channels: Optional[List[SnowflakeID]] = Field(
        None, description="Exempt channel IDs"
    )
    priority: Optional[int] = Field(0, description="Rule priority")
    check_all: Optional[bool] = Field(False, description="Whether to check all rules")
    enabled: Optional[bool] = Field(True, description="Whether rule is enabled")


class AutomodRuleUpdateRequest(BaseModel):
    """Automod rule update request."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    actions: Optional[List[AutomodRuleAction]] = None
    applied_roles: Optional[List[SnowflakeID]] = None
    exempt_roles: Optional[List[SnowflakeID]] = None
    exempt_channels: Optional[List[SnowflakeID]] = None
    priority: Optional[int] = None
    check_all: Optional[bool] = None
    enabled: Optional[bool] = None


class AutomodViolationResponse(BaseModel):
    """Automod violation response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Violation ID")
    user_id: SnowflakeID = Field(..., description="User ID who violated")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    rule_id: SnowflakeID = Field(..., description="Rule ID triggered")
    rule_type: str = Field(..., description="Rule type triggered")
    matched_content: str = Field(..., description="Content that triggered the rule")
    severity: str = Field(..., description="Violation severity")
    actions_taken: List[str] = Field(..., description="Actions that were taken")
    created_at: int = Field(..., description="Violation timestamp")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional details"
    )
