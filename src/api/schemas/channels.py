"""
Channel schemas - Request/response models for channel endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class ChannelCreateRequest(BaseModel):
    """Channel creation request."""
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100, description="Channel name")
    channel_type: str = Field(default="text", description="Channel type: text, voice, category")
    topic: Optional[str] = Field(None, max_length=1024, description="Channel topic")
    category_id: Optional[str] = Field(None, description="Parent category ID")
    nsfw: bool = Field(False, description="NSFW flag")
    slowmode_seconds: int = Field(0, ge=0, le=21600, description="Slowmode delay in seconds")
