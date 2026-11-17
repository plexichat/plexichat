"""
Channel schemas - Request/response models for channel endpoints.
"""

from typing import Optional, Dict
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .common import SnowflakeID, reject_unsafe_ui_text

# Mirror the caps enforced by ``require_invite_limits`` in the channel route.
# The schema is the first line of defence: without these ceilings a client may
# ask for a lifetime of ``2**70`` seconds, which flows into
# ``expires_at = now + (max_age * 1000)`` and into the invite table as an
# unbounded integer. test_schema_owned_hardening_regression asserts these stay
# in sync with the route constants so the two layers cannot drift apart.
MAX_INVITE_AGE_SECONDS = 30 * 24 * 3600
MAX_INVITE_USES = 10_000


class ChannelCreateRequest(BaseModel):
    """Channel creation request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100, description="Channel name")
    channel_type: str = Field(
        default="text",
        pattern="^(text|voice|category)$",
        description="Channel type: text, voice, category",
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

    @field_validator("name", "topic")
    @classmethod
    def _no_ui_xss_chars(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        return reject_unsafe_ui_text(value, info.field_name)


class RecipientResponse(BaseModel):
    """Recipient information in a DM channel."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Recipient user ID")
    username: str = Field(..., description="Recipient username")


class DMChannelResponse(BaseModel):
    """DM channel information response."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Channel ID")
    channel_type: str = Field(default="dm", description="Channel type (dm)")
    recipient_id: Optional[SnowflakeID] = Field(
        default=None, description="Recipient user ID"
    )
    recipient: Optional[RecipientResponse] = Field(
        default=None, description="Recipient details"
    )
    last_message_id: Optional[SnowflakeID] = Field(
        default=None, description="Last message ID in channel"
    )


class DMChannelCreateRequest(BaseModel):
    """DM channel creation request."""

    model_config = ConfigDict(from_attributes=True)
    recipient_id: SnowflakeID = Field(..., description="Recipient user ID")


class NotesChannelResponse(BaseModel):
    """Personal notes channel response."""

    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Channel ID")
    channel_type: str = Field("notes", description="Channel type (notes)")
    name: str = Field("Personal Notes", description="Channel name")
    last_message_id: Optional[SnowflakeID] = Field(None, description="Last message ID")
    last_message_at: Optional[int] = Field(None, description="Last message timestamp")


class ChannelInviteCreateRequest(BaseModel):
    """Channel invite creation request."""

    model_config = ConfigDict(from_attributes=True)
    max_age: int = Field(
        default=86400,
        ge=0,
        le=MAX_INVITE_AGE_SECONDS,
        description="Duration of invite in seconds (0 = never)",
    )
    max_uses: int = Field(
        default=0,
        ge=0,
        le=MAX_INVITE_USES,
        description="Max number of uses (0 = unlimited)",
    )
    temporary: bool = Field(
        default=False, description="Whether this invite grants temporary membership"
    )


class ChannelInviteResponse(BaseModel):
    """Channel invite response."""

    model_config = ConfigDict(from_attributes=True)
    code: str = Field(..., description="Invite code")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    server_id: Optional[SnowflakeID] = Field(default=None, description="Server ID")
    max_age: int = Field(default=86400, description="Max age in seconds")
    max_uses: int = Field(default=0, description="Max uses")
    temporary: bool = Field(default=False, description="Temporary membership")
    uses: int = Field(default=0, description="Number of uses")
    created_at: Optional[int] = Field(default=None, description="Creation timestamp")


class InviteInfoResponse(BaseModel):
    """Invite information response."""

    model_config = ConfigDict(from_attributes=True)
    code: str = Field(..., description="Invite code")
    server_id: Optional[SnowflakeID] = Field(None, description="Server ID")
    server_name: Optional[str] = Field(None, description="Server name")
    channel_id: Optional[SnowflakeID] = Field(None, description="Channel ID")
    inviter_id: Optional[SnowflakeID] = Field(None, description="Inviter user ID")
    uses: int = Field(0, description="Number of uses")
    max_uses: int = Field(0, description="Maximum uses")
    expires_at: Optional[int] = Field(None, description="Expiration timestamp")


class InviteJoinResponse(BaseModel):
    """Invite join response."""

    model_config = ConfigDict(from_attributes=True)
    success: bool = Field(True, description="Whether join was successful")
    server_id: Optional[SnowflakeID] = Field(None, description="Server ID joined")


class AttachmentUploadResponse(BaseModel):
    """Attachment upload response."""

    model_config = ConfigDict(from_attributes=True)
    id: str = Field(..., description="File ID")
    filename: str = Field(..., description="Original filename")
    size: int = Field(..., description="File size in bytes")
    content_type: str = Field(..., description="MIME type")
    url: str = Field(..., description="Download URL")
    hash: Optional[str] = Field(None, description="SHA-256 hash for content reporting")
    thumbnails: Optional[Dict[str, str]] = Field(
        None, description="Generated thumbnails"
    )
