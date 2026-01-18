"""
Admin API schemas.
"""

from typing import List, Optional, Dict
from pydantic import BaseModel, Field, ConfigDict


class AdminLoginRequest(BaseModel):
    """Admin login request."""

    model_config = ConfigDict(from_attributes=True)

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class AdminLoginResponse(BaseModel):
    """Admin login response."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(
        ..., description="Login status (success, otp_required, otp_setup_required)"
    )
    token: Optional[str] = Field(
        default=None, description="Session token if successful"
    )
    admin_id: Optional[str] = Field(
        default=None, description="Admin ID if OTP required"
    )
    otp_secret: Optional[str] = Field(default=None, description="OTP secret for setup")
    otp_qr_uri: Optional[str] = Field(default=None, description="OTP QR URI for setup")
    message: Optional[str] = Field(default=None, description="Instruction message")


class OTPVerifyRequest(BaseModel):
    """OTP verification request."""

    model_config = ConfigDict(from_attributes=True)

    admin_id: str = Field(..., description="Admin ID")
    code: str = Field(..., min_length=6, max_length=8, description="OTP code")
    is_setup: bool = Field(False, description="Whether this is for initial setup")


class TicketStatusUpdate(BaseModel):
    """Update ticket status."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., pattern="^(open|in_progress|resolved|closed)$")


class InternalNoteCreate(BaseModel):
    """Create internal note."""

    model_config = ConfigDict(from_attributes=True)

    content: str = Field(..., min_length=1, max_length=2000)


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


class TicketResponse(BaseModel):
    """Feedback ticket response."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Ticket ID")
    user_id: str = Field(..., description="User ID who submitted feedback")
    username: str = Field(..., description="Username who submitted feedback")
    content: str = Field(..., description="Feedback content")
    category: Optional[str] = Field(None, description="Feedback category")
    rating: Optional[int] = Field(None, description="Feedback rating (1-5)")
    status: str = Field(..., description="Ticket status")
    created_at: int = Field(..., description="Creation timestamp")
    resolved_at: Optional[int] = Field(None, description="Resolution timestamp")
    resolved_by: Optional[str] = Field(None, description="Admin ID who resolved it")


class NoteResponse(BaseModel):
    """Admin note response."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Note ID")
    ticket_id: str = Field(..., description="Ticket ID")
    admin_id: str = Field(..., description="Admin ID who created the note")
    admin_username: str = Field(..., description="Admin username")
    content: str = Field(..., description="Note content")
    created_at: int = Field(..., description="Creation timestamp")


class TelemetryEndpointStat(BaseModel):
    """Statistics for a single endpoint."""

    model_config = ConfigDict(from_attributes=True)

    endpoint: str = Field(..., description="Endpoint path")
    method: str = Field(..., description="HTTP method")
    count: int = Field(..., description="Request count")
    avg_ms: float = Field(..., description="Average response time in ms")
    min_ms: Optional[float] = Field(None, description="Minimum response time in ms")
    max_ms: Optional[float] = Field(None, description="Maximum response time in ms")
    p50_ms: Optional[float] = Field(
        None, description="50th percentile response time in ms"
    )
    p95_ms: float = Field(..., description="95th percentile response time in ms")
    p99_ms: Optional[float] = Field(
        None, description="99th percentile response time in ms"
    )
    error_rate: float = Field(..., description="Error rate percentage")


class AdminDashboardResponse(BaseModel):
    """Admin dashboard data."""

    model_config = ConfigDict(from_attributes=True)

    tickets: Dict[str, int] = Field(..., description="Ticket counts by status")
    telemetry: List[TelemetryEndpointStat] = Field(
        ..., description="Top telemetry stats"
    )


class TelemetryStatsResponse(BaseModel):
    """Telemetry statistics response."""

    model_config = ConfigDict(from_attributes=True)

    stats: List[TelemetryEndpointStat] = Field(..., description="Endpoint statistics")
    source: str = Field(..., description="Data source filter applied")


class TelemetryHistoryBucket(BaseModel):
    """A single time bucket in telemetry history."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: int = Field(..., description="Bucket start timestamp")
    avg_ms: float = Field(..., description="Average response time in this bucket")
    count: int = Field(..., description="Request count in this bucket")
    error_count: int = Field(..., description="Error count in this bucket")


class TelemetryHistoryResponse(BaseModel):
    """Telemetry history response."""

    model_config = ConfigDict(from_attributes=True)

    history: List[TelemetryHistoryBucket] = Field(
        ..., description="History data buckets"
    )


class TelemetryResetResponse(BaseModel):
    """Telemetry reset response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether reset was successful")
    deleted_count: int = Field(..., description="Number of records deleted")


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

    open: int = Field(0, description="Open reports")
    reviewed: int = Field(0, description="Reviewed reports")
    total: int = Field(0, description="Total reports")


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


class UserTierUpdate(BaseModel):
    """Update user tier."""

    model_config = ConfigDict(from_attributes=True)

    tier: str = Field(..., pattern="^(free|alpha|beta|premium|staff)$")


class UserSearchResponse(BaseModel):
    """User search result."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="User ID as string")
    username: str = Field(..., description="Username")
    email: Optional[str] = Field(None, description="Email address")
    tier: str = Field(..., description="User tier")
    badges: List[str] = Field(..., description="User badges")
    created_at: int = Field(..., description="Creation timestamp")


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
    last_login: Optional[int] = Field(None, description="Last login timestamp")
    account_locked: bool = Field(False, description="Whether account is locked")
    locked_until: Optional[int] = Field(None, description="Lock expiration timestamp")


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


class BlockUserResponse(BaseModel):
    """Response for user block."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether block was successful")
    user_id: int = Field(..., description="User ID")


class IPBlockRequest(BaseModel):
    """Request to block an IP address."""
    ip_address: str = Field(..., description="IP address to block")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for blocking")
    duration_hours: Optional[int] = Field(None, description="Duration in hours")


class BlockedIPResponse(BaseModel):
    """Blocked IP information."""
    ip_address: str
    reason: Optional[str]
    blocked_at: int
    blocked_by: Optional[int]
    expires_at: Optional[int]


class ForceLogoutRequest(BaseModel):
    """Request to force logout a user."""
    user_id: str = Field(..., description="User ID to logout")


class UserLockRequest(BaseModel):
    """Request to lock/suspend a user account."""
    user_id: str = Field(..., description="User ID to lock")
    duration_seconds: Optional[int] = Field(None, description="Lock duration in seconds (null for permanent)")


class TelemetryExportResponse(BaseModel):
    """Telemetry export response (JSON format)."""

    model_config = ConfigDict(from_attributes=True)

    export_time: str = Field(..., description="Export generation time")
    hours: int = Field(..., description="Time range in hours")
    stats: List[TelemetryEndpointStat] = Field(..., description="Exported statistics")


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
