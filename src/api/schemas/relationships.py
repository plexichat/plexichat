"""
Relationship schemas - Request/response models for relationship endpoints.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class FriendRequestCreate(BaseModel):
    """Friend request creation model."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str = Field(..., description="Target user ID")
    message: Optional[str] = Field(None, max_length=256, description="Optional message")


class BlockCreate(BaseModel):
    """Block creation model."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str = Field(..., description="User ID to block")


class RelationshipResponse(BaseModel):
    """Relationship response model."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str = Field(..., description="Related user ID")
    status: str = Field(..., description="Relationship status")
    created_at: Optional[int] = Field(None, description="Creation timestamp")
    
    @field_serializer("user_id")
    def serialize_user_id(self, v: Any) -> Optional[str]:
        return str(v) if v else None


class FriendRequestResponse(BaseModel):
    """Friend request response model."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="Request ID")
    sender_id: str = Field(..., description="Sender user ID")
    recipient_id: str = Field(..., description="Recipient user ID")
    message: Optional[str] = Field(None, description="Request message")
    status: str = Field(..., description="Request status")
    created_at: int = Field(..., description="Creation timestamp")
    
    @field_serializer("id", "sender_id", "recipient_id")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None
