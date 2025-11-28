"""
Reaction schemas - Request/response models for reaction endpoints.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class ReactionResponse(BaseModel):
    """Reaction response model."""
    model_config = ConfigDict(from_attributes=True)
    
    emoji: str = Field(..., description="Emoji identifier")
    count: int = Field(..., description="Number of reactions")
    me: bool = Field(False, description="Whether current user reacted")


class ReactionUserResponse(BaseModel):
    """User who reacted response model."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str = Field(..., description="User ID")
    reacted_at: int = Field(..., description="Reaction timestamp")
    
    @field_serializer("user_id")
    def serialize_user_id(self, v: Any) -> str:
        return str(v) if v else None


class MessageReactionsResponse(BaseModel):
    """All reactions on a message response model."""
    model_config = ConfigDict(from_attributes=True)
    
    message_id: str = Field(..., description="Message ID")
    reactions: list = Field(default_factory=list, description="List of reactions")
    total_count: int = Field(0, description="Total reaction count")
    
    @field_serializer("message_id")
    def serialize_message_id(self, v: Any) -> str:
        return str(v) if v else None
