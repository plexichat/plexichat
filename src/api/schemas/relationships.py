"""
Relationship schemas - Request/response models for relationship endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class FriendRequestCreate(BaseModel):
    """Friend request creation model."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    user_id: SnowflakeID = Field(..., description="Target user ID")
    message: Optional[str] = Field(None, max_length=256, description="Optional message")


class BlockCreate(BaseModel):
    """Block creation model."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    user_id: SnowflakeID = Field(..., description="User ID to block")


class RelationshipResponse(BaseModel):
    """Relationship response model."""
    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID = Field(..., description="Related user ID")
    status: str = Field(..., description="Relationship status")
    created_at: Optional[int] = Field(None, description="Creation timestamp")


class FriendRequestResponse(BaseModel):
    """Friend request response model."""
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Request ID")
    sender_id: SnowflakeID = Field(..., description="Sender user ID")
    recipient_id: SnowflakeID = Field(..., description="Recipient user ID")
    message: Optional[str] = Field(None, description="Request message")
    status: str = Field(..., description="Request status")
    created_at: int = Field(..., description="Creation timestamp")
