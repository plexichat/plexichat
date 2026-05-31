"""
Moderation report schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ModerationReportCountsResponse(BaseModel):
    """Counts for message or user moderation reports."""

    model_config = ConfigDict(from_attributes=True)

    pending: int = Field(0, description="Pending reports")
    reviewed: int = Field(0, description="Reviewed reports")
    actioned: int = Field(0, description="Actioned reports")
    dismissed: int = Field(0, description="Dismissed reports")
    total: int = Field(0, description="Total reports")


class ModerationReportReviewRequest(BaseModel):
    """Review a message or user report."""

    model_config = ConfigDict(from_attributes=True)

    action: str = Field(..., pattern="^(action|dismiss|review)$")
    notes: Optional[str] = Field(None, max_length=2000)


class ModerationReportReviewResponse(BaseModel):
    """Review result for a message or user report."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the review succeeded")
    action: str = Field(..., description="Action that was recorded")


class MessageReportResponse(BaseModel):
    """Message report row for admin review."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    channel_id: str
    server_id: Optional[str] = None
    reporter_id: str
    reported_user_id: str
    reason: str
    category: str
    details: Optional[str] = None
    message_content: Optional[str] = None
    status: str
    reported_at: int
    reviewed_at: Optional[int] = None
    reviewed_by: Optional[str] = None
    admin_notes: Optional[str] = None
    action_taken: Optional[str] = None


class UserReportResponse(BaseModel):
    """User report row for admin review."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    reported_user_id: str
    reporter_id: str
    reason: str
    category: str
    details: Optional[str] = None
    evidence_message_ids: List[str] = Field(default_factory=list)
    status: str
    reported_at: int
    reviewed_at: Optional[int] = None
    reviewed_by: Optional[str] = None
    admin_notes: Optional[str] = None
    action_taken: Optional[str] = None


class BlockUserRequest(BaseModel):
    """Block a user from uploading media."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int = Field(..., description="User ID to block")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for blocking"
    )
    duration_hours: Optional[int] = Field(
        None, description="Duration in hours (None = permanent)"
    )


class BlockedUserResponse(BaseModel):
    """Blocked user information."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int = Field(..., description="User ID")
    username: Optional[str] = Field(default=None, description="Username")
    reason: str = Field(..., description="Reason for blocking")
    blocked_at: int = Field(..., description="Block timestamp")
    blocked_by: Optional[int] = Field(default=None, description="Admin ID who blocked")
    expires_at: Optional[int] = Field(default=None, description="Expiration timestamp")


class BlockUserResponse(BaseModel):
    """Response for user block."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether block was successful")
    user_id: int = Field(..., description="User ID")
