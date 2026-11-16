"""
Webhook schemas - Request/response models for webhook endpoints.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .policy_types import UIText

from .common import SnowflakeID


def _validate_avatar_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("avatar_url must be an absolute http(s) URL")
    return value


class WebhookCreateRequest(BaseModel):
    """Webhook creation request."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: SnowflakeID = Field(..., description="Channel ID for the webhook")
    name: UIText = Field(..., min_length=1, max_length=80, description="Webhook name")
    avatar_url: Optional[str] = Field(
        None, max_length=2048, description="Webhook avatar URL"
    )
    require_signed_deliveries: bool = Field(
        False,
        description="When true, executions without a valid Ed25519 signature are rejected even with a valid token",
    )

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, value):
        return _validate_avatar_url(value)


class WebhookUpdateRequest(BaseModel):
    """Webhook update request."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[UIText] = Field(
        None, min_length=1, max_length=80, description="Webhook name"
    )
    avatar_url: Optional[str] = Field(
        None, max_length=2048, description="Webhook avatar URL"
    )
    channel_id: Optional[SnowflakeID] = Field(None, description="New channel ID")
    require_signed_deliveries: Optional[bool] = Field(
        None,
        description="When true, executions without a valid Ed25519 signature are rejected even with a valid token",
    )

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, value):
        return _validate_avatar_url(value)


class WebhookResponse(BaseModel):
    """Webhook response model."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Webhook ID")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    creator_id: SnowflakeID = Field(..., description="Creator user ID")
    name: str = Field(..., description="Webhook name")
    avatar_url: Optional[str] = Field(None, description="Webhook avatar URL")
    token: Optional[str] = Field(None, description="Webhook token (only on create)")
    url: Optional[str] = Field(None, description="Webhook URL (only on create)")
    require_signed_deliveries: bool = Field(
        False,
        description="Whether unsigned executions are rejected even with a valid token",
    )
    created_at: int = Field(..., description="Creation timestamp")


class WebhookExecuteRequest(BaseModel):
    """Webhook execution request."""

    model_config = ConfigDict(from_attributes=True)

    content: Optional[str] = Field(None, max_length=2000, description="Message content")
    username: Optional[UIText] = Field(
        None, max_length=80, description="Override username"
    )
    avatar_url: Optional[str] = Field(None, description="Override avatar URL")
    embeds: Optional[List[Dict[str, Any]]] = Field(None, description="Rich embeds")
    thread_id: Optional[SnowflakeID] = Field(None, description="Thread ID to post to")


class WebhookMessageResponse(BaseModel):
    """Webhook message response model."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Message ID")
    webhook_id: SnowflakeID = Field(..., description="Webhook ID")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    content: Optional[str] = Field(None, description="Message content")
    username: Optional[str] = Field(None, description="Username override")
    avatar_url: Optional[str] = Field(None, description="Avatar override")
    created_at: int = Field(..., description="Creation timestamp")
