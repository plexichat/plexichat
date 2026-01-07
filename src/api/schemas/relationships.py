"""
Relationship schemas - Request/response models for relationship endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class PresenceInfo(BaseModel):
    """Presence information in relationship response."""

    status: str = Field(..., description="User online status")


class DetailedRelationshipInfo(BaseModel):
    """Detailed relationship info including user details."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    avatar_url: Optional[str] = Field(default=None, description="User avatar URL")
    status: str = Field(
        ...,
        description="Relationship status (friend, pending_incoming, pending_outgoing, blocked)",
    )
    presence: Optional[PresenceInfo] = Field(
        default=None, description="User presence status"
    )
    message: Optional[str] = Field(
        default=None, description="Optional message (for pending requests)"
    )
    created_at: Optional[int] = Field(default=None, description="Creation timestamp")


class RelationshipListResponse(BaseModel):
    """List of detailed relationships."""

    relationships: List[DetailedRelationshipInfo] = Field(
        ..., description="List of relationships"
    )


class FriendRequestCreate(BaseModel):
    """Friend request creation model."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    user_id: SnowflakeID = Field(..., description="Target user ID")
    message: Optional[str] = Field(
        default=None, max_length=256, description="Optional message"
    )


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
