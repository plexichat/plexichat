"""
Presence schemas - Request/response models for presence endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class PresenceUpdate(BaseModel):
    """Presence update request."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., description="Status: online, idle, dnd, invisible")
    custom_status: Optional[str] = Field(
        None, max_length=128, description="Custom status text"
    )
    custom_emoji: Optional[str] = Field(None, description="Custom status emoji")


class PresenceResponse(BaseModel):
    """Presence response model."""

    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID = Field(..., description="User ID")
    status: str = Field(..., description="Current status")
    custom_status: Optional[str] = Field(None, description="Custom status text")
    custom_emoji: Optional[str] = Field(None, description="Custom status emoji")
    last_seen: Optional[int] = Field(None, description="Last seen timestamp")


class ActivityResponse(BaseModel):
    """Activity response model."""

    model_config = ConfigDict(from_attributes=True)

    activity_type: str = Field(..., description="Activity type")
    name: str = Field(..., description="Activity name")
    details: Optional[str] = Field(None, description="Activity details")
    state: Optional[str] = Field(None, description="Activity state")
    url: Optional[str] = Field(None, description="Activity URL")
    started_at: Optional[int] = Field(None, description="Activity start timestamp")
