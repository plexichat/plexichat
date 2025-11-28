"""
Webhook schemas - Request/response models for webhook endpoints.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class WebhookCreateRequest(BaseModel):
    """Webhook creation request."""
    model_config = ConfigDict(from_attributes=True)
    
    channel_id: str = Field(..., description="Channel ID for the webhook")
    name: str = Field(..., min_length=1, max_length=80, description="Webhook name")
    avatar_url: Optional[str] = Field(None, description="Webhook avatar URL")


class WebhookUpdateRequest(BaseModel):
    """Webhook update request."""
    model_config = ConfigDict(from_attributes=True)
    
    name: Optional[str] = Field(None, min_length=1, max_length=80, description="Webhook name")
    avatar_url: Optional[str] = Field(None, description="Webhook avatar URL")
    channel_id: Optional[str] = Field(None, description="New channel ID")


class WebhookResponse(BaseModel):
    """Webhook response model."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="Webhook ID")
    channel_id: str = Field(..., description="Channel ID")
    server_id: str = Field(..., description="Server ID")
    creator_id: str = Field(..., description="Creator user ID")
    name: str = Field(..., description="Webhook name")
    avatar_url: Optional[str] = Field(None, description="Webhook avatar URL")
    token: Optional[str] = Field(None, description="Webhook token (only on create)")
    url: Optional[str] = Field(None, description="Webhook URL (only on create)")
    created_at: int = Field(..., description="Creation timestamp")
    
    @field_serializer("id", "channel_id", "server_id", "creator_id")
    def serialize_ids(self, v: Any) -> str:
        return str(v) if v else None


class WebhookExecuteRequest(BaseModel):
    """Webhook execution request."""
    model_config = ConfigDict(from_attributes=True)
    
    content: Optional[str] = Field(None, max_length=2000, description="Message content")
    username: Optional[str] = Field(None, max_length=80, description="Override username")
    avatar_url: Optional[str] = Field(None, description="Override avatar URL")
    embeds: Optional[List[Dict[str, Any]]] = Field(None, description="Rich embeds")
    thread_id: Optional[str] = Field(None, description="Thread ID to post to")


class WebhookMessageResponse(BaseModel):
    """Webhook message response model."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="Message ID")
    webhook_id: str = Field(..., description="Webhook ID")
    channel_id: str = Field(..., description="Channel ID")
    content: Optional[str] = Field(None, description="Message content")
    username: Optional[str] = Field(None, description="Username override")
    avatar_url: Optional[str] = Field(None, description="Avatar override")
    created_at: int = Field(..., description="Creation timestamp")
    
    @field_serializer("id", "webhook_id", "channel_id")
    def serialize_ids(self, v: Any) -> str:
        return str(v) if v else None
