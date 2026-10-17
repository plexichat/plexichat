"""
User schemas - Request/response models for user endpoints.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class UserUpdateRequest(BaseModel):
    """User update request.

    ``username``/``email``/``password`` are account credentials handled by the
    auth module; the profile fields below are forwarded to the profile manager
    so PATCH /users/@me can update them (empty string clears a field).
    """

    model_config = ConfigDict(from_attributes=True)

    username: Optional[str] = Field(
        None, min_length=3, max_length=32, description="New username"
    )
    email: Optional[str] = Field(None, description="New email")
    avatar_url: Optional[str] = Field(
        None,
        description=(
            "Avatar URL. Must reference the user's uploaded avatar "
            "(/api/v1/avatars/users/{user_id}); arbitrary URLs are rejected."
        ),
    )
    password: Optional[str] = Field(None, description="New password")
    current_password: Optional[str] = Field(
        None, description="Current password (required for password change)"
    )
    bio: Optional[str] = Field(
        None, max_length=1000, description="Profile bio (empty string clears)"
    )
    pronouns: Optional[str] = Field(
        None, max_length=40, description="Pronouns (empty string clears)"
    )
    location: Optional[str] = Field(
        None, max_length=100, description="Location (empty string clears)"
    )
    timezone: Optional[str] = Field(
        None, max_length=64, description="Timezone (empty string clears)"
    )
    banner_url: Optional[str] = Field(
        None, description="Banner image URL (empty string clears)"
    )
    social_links: Optional[List[dict]] = Field(
        None, description="Social link objects (empty list clears all)"
    )


class UserDeletionRequest(BaseModel):
    """Account deletion request."""

    model_config = ConfigDict(from_attributes=True)

    password: str = Field(..., description="Current password for verification")
    code: Optional[str] = Field(None, description="2FA code (required if enabled)")


class UserPublicResponse(BaseModel):
    """Public user information response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    created_at: int = Field(..., description="Account creation timestamp")
    badges: list[str] = Field(default_factory=list, description="User profile badges")


class UserAvatarResponse(BaseModel):
    """User avatar upload response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the upload was successful")
    avatar_url: str = Field(..., description="The URL of the uploaded avatar")
    width: int = Field(..., description="Avatar width in pixels")
    height: int = Field(..., description="Avatar height in pixels")
    size: int = Field(..., description="Avatar file size in bytes")
    animated: bool = Field(False, description="Whether the avatar is animated")
