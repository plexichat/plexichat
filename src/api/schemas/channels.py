"""
Channel schemas - Request/response models for channel endpoints.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class ChannelCreateRequest(BaseModel):
    """Channel creation request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100, description="Channel name")
    channel_type: str = Field(
        default="text", description="Channel type: text, voice, category"
    )
    topic: Optional[str] = Field(None, max_length=1024, description="Channel topic")
    category_id: Optional[SnowflakeID] = Field(None, description="Parent category ID")
    nsfw: bool = Field(False, description="NSFW flag")
    slowmode_seconds: int = Field(
        0, ge=0, le=21600, description="Slowmode delay in seconds"
    )


class RecipientResponse(BaseModel):
    """Recipient information in a DM channel."""
    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Recipient user ID")
    username: str = Field(..., description="Recipient username")


class DMChannelResponse(BaseModel):
    """DM channel information response."""
    model_config = ConfigDict(from_attributes=True)
    id: SnowflakeID = Field(..., description="Channel ID")
    channel_type: str = Field("dm", description="Channel type (dm)")
    recipient_id: Optional[SnowflakeID] = Field(None, description="Recipient user ID")
    recipient: Optional[RecipientResponse] = Field(None, description="Recipient details")
    last_message_id: Optional[SnowflakeID] = Field(None, description="Last message ID in channel")


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
    max_age: int = Field(86400, ge=0, description="Duration of invite in seconds (0 = never)")
    max_uses: int = Field(0, ge=0, description="Max number of uses (0 = unlimited)")
    temporary: bool = Field(False, description="Whether this invite grants temporary membership")


class ChannelInviteResponse(BaseModel):
    """Channel invite response."""
    model_config = ConfigDict(from_attributes=True)
    code: str = Field(..., description="Invite code")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    server_id: Optional[SnowflakeID] = Field(None, description="Server ID")
    max_age: int = Field(..., description="Max age in seconds")
    max_uses: int = Field(..., description="Max uses")
    temporary: bool = Field(..., description="Temporary membership")
    uses: int = Field(0, description="Number of uses")
    created_at: Optional[int] = Field(None, description="Creation timestamp")


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
    thumbnails: Optional[Dict[str, str]] = Field(None, description="Generated thumbnails")

