"""
Message schemas - Request/response models for message endpoints.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID
from .reactions import ReactionResponse


class AttachmentRequest(BaseModel):
    """Attachment in message request."""

    model_config = ConfigDict(from_attributes=True)

    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., ge=0, description="File size in bytes")
    url: str = Field(..., description="File URL")
    hash: Optional[str] = Field(None, description="SHA-256 hash for content reporting")


class MessageCreateRequest(BaseModel):
    """Message creation request."""

    model_config = ConfigDict(from_attributes=True)

    content: Optional[str] = Field(None, max_length=4000, description="Message content")
    reply_to_id: Optional[SnowflakeID] = Field(
        None, description="ID of message to reply to"
    )
    attachments: Optional[List[AttachmentRequest]] = Field(
        None, description="Message attachments"
    )
    embeds: Optional[List[Dict[str, Any]]] = Field(None, description="Rich embeds")


class MessageUpdateRequest(BaseModel):
    """Message update request."""

    model_config = ConfigDict(from_attributes=True)

    content: str = Field(..., max_length=4000, description="New message content")


class AttachmentResponse(BaseModel):
    """Attachment response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Attachment ID")
    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., description="File size in bytes")
    url: str = Field(..., description="File URL")
    hash: Optional[str] = Field(None, description="SHA-256 hash for content reporting")


class MessageResponse(BaseModel):
    """Message response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Message ID")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    author_id: SnowflakeID = Field(..., description="Author user ID")
    content: Optional[str] = Field(None, description="Message content")
    created_at: int = Field(..., description="Creation timestamp")
    edited_at: Optional[int] = Field(None, description="Edit timestamp")
    reply_to_id: Optional[SnowflakeID] = Field(
        None, description="Reply target message ID"
    )
    attachments: List[AttachmentResponse] = Field(
        default_factory=list, description="Attachments"
    )
    embeds: List[Dict[str, Any]] = Field(
        default_factory=list, description="Rich embeds"
    )
    pinned: bool = Field(False, description="Pinned status")
    status: Optional[str] = Field(
        None, description="Status for the current user (sent, delivered, read)"
    )
    delivery_count: int = Field(
        0, description="Number of users who received the message"
    )
    read_count: int = Field(0, description="Number of users who read the message")
    read_by: List[str] = Field(
        default_factory=list, description="List of usernames who have read the message"
    )
    author_username: Optional[str] = Field(None, description="Author's username")
    author_avatar_url: Optional[str] = Field(None, description="Author's avatar URL")
    reactions: List[ReactionResponse] = Field(
        default_factory=list, description="Message reactions"
    )


class MessagingSettingsResponse(BaseModel):
    """User messaging settings response."""

    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID = Field(..., description="User ID")
    read_receipts_enabled: bool = Field(
        True, description="Whether to send read receipts"
    )
    typing_indicators_enabled: bool = Field(
        True, description="Whether to show typing indicators"
    )
    compact_messages_enabled: bool = Field(
        True, description="Whether to group consecutive messages from the same person"
    )
    allow_dms_from: str = Field(
        "everyone", description="Who can send DMs (everyone, friends, none)"
    )
    auto_create_dms: bool = Field(
        True, description="Whether to automatically create DM conversations"
    )
    max_message_length: Optional[int] = Field(
        None, description="Maximum message length (None = global default)"
    )
    max_attachment_size: Optional[int] = Field(
        None, description="Maximum attachment size (None = global default)"
    )
    max_attachments_per_message: Optional[int] = Field(
        None, description="Maximum attachments per message (None = global default)"
    )


class MessagingSettingsUpdateRequest(BaseModel):
    """User messaging settings update request."""

    model_config = ConfigDict(from_attributes=True)

    read_receipts_enabled: Optional[bool] = Field(
        None, description="Whether to send read receipts"
    )
    typing_indicators_enabled: Optional[bool] = Field(
        None, description="Whether to show typing indicators"
    )
    compact_messages_enabled: Optional[bool] = Field(
        None, description="Whether to group consecutive messages from the same person"
    )
    allow_dms_from: Optional[str] = Field(
        None, description="Who can send DMs (everyone, friends, none)"
    )
    auto_create_dms: Optional[bool] = Field(
        None, description="Whether to automatically create DM conversations"
    )
    max_message_length: Optional[int] = Field(
        None, description="Maximum message length"
    )
    max_attachment_size: Optional[int] = Field(
        None, description="Maximum attachment size"
    )
    max_attachments_per_message: Optional[int] = Field(
        None, description="Maximum attachments per message"
    )


class UnreadCountResponse(BaseModel):
    """Unread message count response."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: SnowflakeID = Field(..., description="Channel ID")
    unread_count: int = Field(..., description="Number of unread messages")


class AllUnreadCountsResponse(BaseModel):
    """All unread message counts response."""

    model_config = ConfigDict(from_attributes=True)

    unread_counts: Dict[str, int] = Field(
        ..., description="Map of channel IDs to unread counts"
    )


class AckResponse(BaseModel):
    """Message acknowledgement response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(True, description="Whether operation was successful")
    messages_marked: int = Field(..., description="Number of messages marked as read")
