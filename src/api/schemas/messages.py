"""
Message schemas - Request/response models for message endpoints.
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID
from .reactions import ReactionResponse
from .polls import PollInlineCreateRequest, PollResponse, PollResultsResponse


class AttachmentRequest(BaseModel):
    """Attachment in message request."""

    model_config = ConfigDict(from_attributes=True)

    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., ge=0, description="File size in bytes")
    url: str = Field(..., description="File URL")
    hash: Optional[str] = Field(None, description="SHA-256 hash for content reporting")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Attachment metadata")


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
    poll: Optional[PollInlineCreateRequest] = Field(
        None, description="Poll to attach to this message"
    )


class MessageUpdateRequest(BaseModel):
    """Message update request."""

    model_config = ConfigDict(from_attributes=True)

    content: str = Field(..., max_length=4000, description="New message content")


class BulkDeleteRequest(BaseModel):
    """Bulk-delete messages request."""

    model_config = ConfigDict(from_attributes=True)

    message_ids: List[SnowflakeID] = Field(
        ..., description="Message IDs to delete", max_length=100
    )


class AttachmentResponse(BaseModel):
    """Attachment response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Attachment ID")
    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., description="File size in bytes")
    url: str = Field(..., description="File URL")
    hash: Optional[str] = Field(None, description="SHA-256 hash for content reporting")


class ReaderInfo(BaseModel):
    """Information about a user who read a message."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")


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
    read: bool = Field(
        False, description="Whether the current user has read this message"
    )
    read_by: List[ReaderInfo] = Field(
        default_factory=list, description="List of users who have read the message"
    )
    author_username: Optional[str] = Field(None, description="Author's username")
    author_avatar_url: Optional[str] = Field(None, description="Author's avatar URL")
    author_badges: List[str] = Field(
        default_factory=list, description="Author's profile badges"
    )
    reactions: List[ReactionResponse] = Field(
        default_factory=list, description="Message reactions"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional message metadata"
    )
    poll: Optional[PollResponse] = Field(None, description="Attached poll details")
    poll_results: Optional[PollResultsResponse] = Field(
        None, description="Current poll results and user voting status"
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


class LastReadResponse(BaseModel):
    """Last read message position response."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: SnowflakeID = Field(..., description="Channel ID")
    last_read_message_id: Optional[SnowflakeID] = Field(
        None, description="ID of the last read message"
    )
    last_read_at: Optional[int] = Field(None, description="Timestamp of last read")


class MarkUnreadRequest(BaseModel):
    """Mark unread request."""

    model_config = ConfigDict(from_attributes=True)

    message_id: Optional[SnowflakeID] = Field(
        None, description="Mark as unread from this message onwards"
    )


class MarkUnreadResponse(BaseModel):
    """Mark unread response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(True, description="Whether operation was successful")
    unread_count: int = Field(..., description="New unread count")


class TranscriptExportRequest(BaseModel):
    """Transcript export request."""

    model_config = ConfigDict(from_attributes=True)

    format: Literal["json", "csv", "txt", "html"] = Field(
        "json",
        description="Export format: json, csv, txt, html",
    )
    from_date: Optional[str] = Field(None, description="Start date (ISO 8601)")
    to_date: Optional[str] = Field(None, description="End date (ISO 8601)")
    timezone: str = Field("UTC", description="Timezone for dates")


class TranscriptExportResponse(BaseModel):
    """Transcript export response."""

    model_config = ConfigDict(from_attributes=True)

    export_id: str = Field(..., description="Export ID")
    status: str = Field(
        "pending", description="Export status: pending, generating, ready, failed"
    )
    message_count: int = Field(0, description="Number of messages exported")
    file_url: Optional[str] = Field(
        None, description="Download URL for the export file"
    )
    expires_at: Optional[int] = Field(None, description="When the download expires")
    error: Optional[str] = Field(None, description="Error message if failed")


class TranscriptExportStatusResponse(BaseModel):
    """Transcript export status response."""

    model_config = ConfigDict(from_attributes=True)

    export_id: str = Field(..., description="Export ID")
    status: str = Field(..., description="Export status")
    message_count: int = Field(0, description="Number of messages exported")
    file_url: Optional[str] = Field(None, description="Download URL")
    expires_at: Optional[int] = Field(None, description="Expiry timestamp")
    error: Optional[str] = Field(None, description="Error message if failed")


class BulkReadResponse(BaseModel):
    """Bulk read response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(True, description="Whether operation was successful")
    channels_marked: int = Field(..., description="Number of channels marked as read")
