"""
User management schemas.
"""

from typing import List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class UserTierUpdate(BaseModel):
    """Update user tier."""

    model_config = ConfigDict(from_attributes=True)

    tier: str = Field(..., pattern="^(standard|alpha|premium|staff)$")


class UserSearchResponse(BaseModel):
    """User search result."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="User ID as string")
    username: str = Field(..., description="Username")
    email: Optional[str] = Field(None, description="Email address")
    tier: str = Field(..., description="User tier")
    badges: List[str] = Field(..., description="User badges")
    created_at: int = Field(..., description="Creation timestamp")
    deletion_status: str = Field("active", description="Account deletion status")
    deletion_at: Optional[int] = Field(None, description="Scheduled deletion timestamp")


class UserSearchListResponse(BaseModel):
    """List of user search results."""

    model_config = ConfigDict(from_attributes=True)

    users: List[UserSearchResponse] = Field(..., description="Search results")


class UserDetailsResponse(BaseModel):
    """Detailed user information for admin."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="User ID as string")
    username: str = Field(..., description="Username")
    email: Optional[str] = Field(None, description="Email address")
    tier: str = Field(..., description="User tier")
    badges: List[str] = Field(..., description="User badges")
    created_at: int = Field(..., description="Creation timestamp")
    deletion_status: str = Field("active", description="Account deletion status")
    deletion_at: Optional[int] = Field(None, description="Scheduled deletion timestamp")
    last_login: Optional[int] = Field(None, description="Last login timestamp")
    account_locked: bool = Field(False, description="Whether account is locked")
    locked_until: Optional[int] = Field(None, description="Lock expiration timestamp")
    force_username_change: bool = Field(
        False, description="Whether user must change name"
    )


class BannedUsernameResponse(BaseModel):
    """Banned username pattern response."""

    id: int
    pattern: str
    is_regex: bool
    reason: Optional[str]
    created_at: Union[str, datetime]


class BannedUsernameCreate(BaseModel):
    """Create banned username pattern."""

    pattern: str = Field(..., min_length=1, max_length=100)
    reason: Optional[str] = Field(None, max_length=200)
    is_regex: bool = Field(False)


class UserNotesResponse(BaseModel):
    """Response for user notes."""

    user_id: str
    notes: str


class UserNotesUpdate(BaseModel):
    """Request to update user notes."""

    notes: str = Field(..., max_length=5000)


class UserTierUpdateResponse(BaseModel):
    """Response for user tier update."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether update was successful")
    user_id: str = Field(..., description="User ID")
    tier: str = Field(..., description="New tier")


class UserBadgeUpdateResponse(BaseModel):
    """Response for user badge update."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether update was successful")
    badges: List[str] = Field(..., description="Updated list of badges")


class ScheduledDeletionResponse(BaseModel):
    """Information about a scheduled account deletion."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    scheduled_at: int = Field(..., description="Timestamp when deletion was scheduled")
    deletion_at: int = Field(
        ..., description="Timestamp when permanent purge will occur"
    )
    days_left: int = Field(..., description="Approximate days remaining until purge")


class ScheduledDeletionListResponse(BaseModel):
    """List of scheduled deletions."""

    model_config = ConfigDict(from_attributes=True)

    deletions: List[ScheduledDeletionResponse] = Field(
        ..., description="Scheduled deletions"
    )


class ForceLogoutRequest(BaseModel):
    """Request to force logout a user."""

    user_id: str = Field(..., description="User ID to logout")


class ForceUsernameChangeRequest(BaseModel):
    """Request to force a username change."""

    ban_current: bool = Field(
        False, description="Whether to add current username to blacklist"
    )
    reason: Optional[str] = Field("Forced change by admin", max_length=200)


class UserLockRequest(BaseModel):
    """Request to lock/suspend a user account."""

    user_id: str = Field(..., description="User ID to lock")
    duration_seconds: Optional[int] = Field(
        None, description="Lock duration in seconds (null for permanent)"
    )


class AvailableTierInfo(BaseModel):
    """Information about an available tier."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Tier ID")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="Tier description")


class AvailableTiersResponse(BaseModel):
    """Available tiers response."""

    model_config = ConfigDict(from_attributes=True)

    tiers: List[AvailableTierInfo] = Field(..., description="List of available tiers")


class AvailableBadgesResponse(BaseModel):
    """Available badges response."""

    model_config = ConfigDict(from_attributes=True)

    badges: List[str] = Field(..., description="List of available badge names")
