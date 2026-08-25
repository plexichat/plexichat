"""
Soundboard schemas - Pydantic models for soundboard endpoints.
"""

from typing import Optional
from typing_extensions import Annotated
from pydantic import BaseModel, Field, ConfigDict, BeforeValidator


def _to_str(v):
    return str(v) if v is not None else v


class SoundResponse(BaseModel):
    """Soundboard sound response model."""

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[str, BeforeValidator(_to_str)] = Field(..., description="Sound ID")
    server_id: Annotated[str, BeforeValidator(_to_str)] = Field(
        ..., description="Server ID"
    )
    name: str = Field(..., description="Sound name")
    format: str = Field(..., description="Sound format (mp3, ogg)")
    emoji: Optional[str] = Field(None, description="Related unicode emoji")
    url: str = Field(..., description="Sound file URL")
    size: int = Field(..., description="File size in bytes")
    duration_seconds: float = Field(..., description="Sound duration in seconds")
    volume: float = Field(1.0, description="Default volume (0.0-1.0)")
    cooldown_seconds: int = Field(
        0, description="Explicit per-sound cooldown (0 = use server default)"
    )
    created_by: Annotated[str, BeforeValidator(_to_str)] = Field(
        ..., description="Creator user ID"
    )
    created_at: int = Field(..., description="Creation timestamp")
    usage_count: int = Field(0, description="Total play count")


class SoundUpdateRequest(BaseModel):
    """Editable sound metadata."""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    emoji: Optional[str] = Field(None, max_length=32)
    volume: Optional[float] = Field(None, ge=0.0, le=1.0)
    cooldown: Optional[int] = Field(None, ge=0)


class SoundPermissionRequest(BaseModel):
    """Role permission entry for a sound."""

    can_use: bool = Field(..., description="Whether the role may play the sound")


class SoundPlayRequest(BaseModel):
    """Playback trigger payload."""

    channel_id: str = Field(..., description="Voice channel ID")
    volume: Optional[float] = Field(None, ge=0.0, le=1.0)


class SoundPermissionResponse(BaseModel):
    """Result of setting a role permission entry."""

    id: Annotated[str, BeforeValidator(_to_str)] = Field(..., description="Entry ID")
    sound_id: Annotated[str, BeforeValidator(_to_str)] = Field(..., description="Sound")
    role_id: Annotated[str, BeforeValidator(_to_str)] = Field(..., description="Role")
    can_use: bool = Field(..., description="Allow/deny flag")


class SoundPlaybackResponse(BaseModel):
    """Playback acknowledgement."""

    sound: SoundResponse = Field(..., description="Played sound")
    user_id: Annotated[str, BeforeValidator(_to_str)] = Field(
        ..., description="Playing user"
    )
    channel_id: Annotated[str, BeforeValidator(_to_str)] = Field(
        ..., description="Voice channel"
    )
    timestamp: int = Field(..., description="Playback timestamp")
