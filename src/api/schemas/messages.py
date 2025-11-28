"""
Message schemas - Request/response models for message endpoints.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class AttachmentRequest(BaseModel):
    """Attachment in message request."""
    model_config = ConfigDict(from_attributes=True)
    
    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., ge=0, description="File size in bytes")
    url: str = Field(..., description="File URL")


class MessageCreateRequest(BaseModel):
    """Message creation request."""
    model_config = ConfigDict(from_attributes=True)
    
    content: Optional[str] = Field(None, max_length=4000, description="Message content")
    reply_to_id: Optional[str] = Field(None, description="ID of message to reply to")
    attachments: Optional[List[AttachmentRequest]] = Field(None, description="Message attachments")
    embeds: Optional[List[Dict[str, Any]]] = Field(None, description="Rich embeds")


class MessageUpdateRequest(BaseModel):
    """Message update request."""
    model_config = ConfigDict(from_attributes=True)
    
    content: str = Field(..., max_length=4000, description="New message content")


class AttachmentResponse(BaseModel):
    """Attachment response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="Attachment ID")
    filename: str = Field(..., description="File name")
    content_type: str = Field(..., description="MIME type")
    size: int = Field(..., description="File size in bytes")
    url: str = Field(..., description="File URL")
    
    @field_serializer("id")
    def serialize_id(self, v: Any) -> str:
        return str(v) if v else None


class MessageResponse(BaseModel):
    """Message response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="Message ID")
    channel_id: str = Field(..., description="Channel ID")
    author_id: str = Field(..., description="Author user ID")
    content: Optional[str] = Field(None, description="Message content")
    created_at: int = Field(..., description="Creation timestamp")
    edited_at: Optional[int] = Field(None, description="Edit timestamp")
    reply_to_id: Optional[str] = Field(None, description="Reply target message ID")
    attachments: List[AttachmentResponse] = Field(default_factory=list, description="Attachments")
    embeds: List[Dict[str, Any]] = Field(default_factory=list, description="Rich embeds")
    pinned: bool = Field(False, description="Pinned status")
    
    @field_serializer("id", "channel_id", "author_id", "reply_to_id")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None
