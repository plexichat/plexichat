"""
Emoji schemas - Pydantic models for custom emoji endpoints.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class EmojiResponse(BaseModel):
    """Custom emoji response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Emoji ID")
    server_id: str = Field(..., description="Server ID")
    name: str = Field(..., description="Emoji name")
    animated: bool = Field(False, description="Whether emoji is animated")
    url: str = Field("", description="Emoji image URL")
    available: bool = Field(True, description="Whether emoji is available for use")
    created_by: str = Field(..., description="User ID who created the emoji")
    created_at: int = Field(..., description="Creation timestamp")

    @field_serializer("id", "server_id", "created_by")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None


class EmojiCountsResponse(BaseModel):
    """Emoji counts response model."""

    static: int = Field(..., description="Number of static emojis")
    animated: int = Field(..., description="Number of animated emojis")
    max_static: int = Field(..., description="Maximum static emojis allowed")
    max_animated: int = Field(..., description="Maximum animated emojis allowed")


class EmojiUpdateRequest(BaseModel):
    """Emoji update request model."""

    name: Optional[str] = Field(None, description="New emoji name")
