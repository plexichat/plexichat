"""
User schemas - Request/response models for user endpoints.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .policy_types import UIText
from .url_types import HttpUrl, validate_http_url

from .common import SnowflakeID


class UserUpdateRequest(BaseModel):
    """User update request.

    ``username``/``email``/``password`` are account credentials handled by the
    auth module; the profile fields below are forwarded to the profile manager
    so PATCH /users/@me can update them (empty string clears a field).
    """

    model_config = ConfigDict(from_attributes=True)

    username: Optional[UIText] = Field(
        None, min_length=3, max_length=32, description="New username"
    )
    email: Optional[str] = Field(None, max_length=254, description="New email")
    avatar_url: Optional[str] = Field(
        None,
        max_length=2048,
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
    banner_url: Optional[HttpUrl] = Field(
        None, max_length=2048, description="Banner image URL (empty string clears)"
    )

    social_links: Optional[List[dict]] = Field(
        None, max_length=20, description="Social link objects (empty list clears all)"
    )

    @field_validator("social_links")
    @classmethod
    def _social_links(cls, value: Optional[List[dict]]) -> Optional[List[dict]]:
        if value is None:
            return value

        for link in value:
            if not isinstance(link, dict):
                raise ValueError("social_links must be objects")
            url = link.get("url")
            if not isinstance(url, str):
                raise ValueError("social link url must be a string")
            validate_http_url(url, "social link url")
            platform = link.get("platform", "")
            if not isinstance(platform, str) or len(platform) > 32:
                raise ValueError(
                    "social link platform must be a string of at most 32 characters"
                )
        return value


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
