"""
User schemas - Request/response models for user endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class UserUpdateRequest(BaseModel):
    """User update request."""
    model_config = ConfigDict(from_attributes=True)

    username: Optional[str] = Field(None, min_length=3, max_length=32, description="New username")
    email: Optional[str] = Field(None, description="New email")
    avatar_url: Optional[str] = Field(None, description="New avatar URL")
    password: Optional[str] = Field(None, description="New password")
    current_password: Optional[str] = Field(None, description="Current password (required for password change)")


class UserPublicResponse(BaseModel):
    """Public user information response."""
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    created_at: int = Field(..., description="Account creation timestamp")
