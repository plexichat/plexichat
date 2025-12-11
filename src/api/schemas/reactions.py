"""
Reaction schemas - Request/response models for reaction endpoints.
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class ReactionResponse(BaseModel):
    """Reaction response model."""
    model_config = ConfigDict(from_attributes=True)

    emoji: str = Field(..., description="Emoji identifier")
    count: int = Field(..., description="Number of reactions")
    me: bool = Field(False, description="Whether current user reacted")


class ReactionUserResponse(BaseModel):
    """User who reacted response model."""
    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID = Field(..., description="User ID")
    reacted_at: int = Field(..., description="Reaction timestamp")


class MessageReactionsResponse(BaseModel):
    """All reactions on a message response model."""
    model_config = ConfigDict(from_attributes=True)

    message_id: SnowflakeID = Field(..., description="Message ID")
    reactions: List[ReactionResponse] = Field(default_factory=list, description="List of reactions")
    total_count: int = Field(0, description="Total reaction count")
