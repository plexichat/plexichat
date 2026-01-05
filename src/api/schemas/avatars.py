"""
Avatar schemas - Pydantic models for avatar and icon endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class AvatarUploadResponse(BaseModel):
    """Response after uploading a user avatar."""
    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the upload was successful")
    avatar_url: str = Field(..., description="The URL of the uploaded avatar")
    width: int = Field(..., description="Image width in pixels")
    height: int = Field(..., description="Image height in pixels")
    size: int = Field(..., description="File size in bytes")
    animated: bool = Field(..., description="Whether the image is animated (GIF/WebP)")


class IconUploadResponse(BaseModel):
    """Response after uploading a server icon."""
    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the upload was successful")
    icon_url: str = Field(..., description="The URL of the uploaded icon")
    width: int = Field(..., description="Image width in pixels")
    height: int = Field(..., description="Image height in pixels")
    size: int = Field(..., description="File size in bytes")
    animated: bool = Field(..., description="Whether the image is animated (GIF/WebP)")
