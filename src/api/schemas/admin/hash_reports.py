"""
Hash report schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class HashReportReviewRequest(BaseModel):
    """Review a hash report."""

    model_config = ConfigDict(from_attributes=True)

    action: str = Field(..., pattern="^(block|clear|dismiss)$")
    notes: Optional[str] = Field(None, max_length=2000)


class ManualBlockHashRequest(BaseModel):
    """Manually block a hash."""

    model_config = ConfigDict(from_attributes=True)

    hash_value: str = Field(..., min_length=64, max_length=128)
    reason: str = Field(..., min_length=1, max_length=500)


class BlockedHashResponse(BaseModel):
    """Blocked hash information."""

    model_config = ConfigDict(from_attributes=True)

    hash_value: str = Field(..., description="Hash value")
    reason: str = Field(..., description="Reason for blocking")
    blocked_at: int = Field(..., description="Block timestamp")
    blocked_by: Optional[int] = Field(default=None, description="Admin ID who blocked")
    auto_blocked: bool = Field(False, description="Whether auto-blocked")
    hash_type: str = Field(..., description="Hash type (sha256, phash)")
    phash_threshold: int = Field(0, description="pHash similarity threshold")


class HashReportResponse(BaseModel):
    """Hash report for admin review."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Report ID")
    hash_value: str = Field(..., description="SHA-256 hash")
    phash_value: Optional[str] = Field(default=None, description="Perceptual hash")
    reporter_id: str = Field(..., description="User ID who reported")
    reporter_username: Optional[str] = Field(
        default=None, description="Username who reported"
    )
    reason: str = Field(..., description="Report reason")
    details: Optional[str] = Field(default=None, description="Report details")
    status: str = Field(..., description="Report status")
    reported_at: int = Field(..., description="Report timestamp")
    reviewed_at: Optional[int] = Field(default=None, description="Review timestamp")
    reviewed_by: Optional[str] = Field(
        default=None, description="Admin ID who reviewed"
    )
    admin_notes: Optional[str] = Field(default=None, description="Admin notes")
    uploader_id: Optional[str] = Field(default=None, description="Uploader user ID")
    message_id: Optional[str] = Field(default=None, description="Message ID")
    attachment_url: Optional[str] = Field(default=None, description="Attachment URL")
    block_uploader: bool = Field(False, description="Whether to block uploader")


class HashReportCountsResponse(BaseModel):
    """Hash report counts by status."""

    model_config = ConfigDict(from_attributes=True)

    pending: int = Field(0, description="Pending reports")
    blocked: int = Field(0, description="Blocked reports")
    cleared: int = Field(0, description="Cleared reports")
    total: int = Field(0, description="Total reports")


class HashReportReviewResponse(BaseModel):
    """Response for hash report review."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether review was successful")
    action: str = Field(..., description="Action taken (block, clear, dismiss)")


class BlockHashResponse(BaseModel):
    """Response for manual hash block."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether block was successful")
    hash_value: str = Field(..., description="Blocked hash value")
    hash_type: str = Field(..., description="Hash type (sha256, phash)")
