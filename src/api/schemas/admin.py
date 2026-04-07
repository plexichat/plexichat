"""
Admin API schemas.
"""

from typing import List, Optional, Dict, Union, Any
from datetime import datetime
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
    challenge_token: Optional[str] = Field(
        default=None, description="Short-lived challenge token for OTP verification"
    )


class OTPVerifyRequest(BaseModel):
    """OTP verification request."""

    model_config = ConfigDict(from_attributes=True)

    admin_id: str = Field(..., description="Admin ID")
    code: str = Field(..., min_length=6, max_length=8, description="OTP code")
    is_setup: bool = Field(False, description="Whether this is for initial setup")
    challenge_token: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description="Challenge token from login step",
    )


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
    error_count: int = Field(0, description="Total error count")
    avg_queries: Optional[float] = Field(
        0.0, description="Average DB queries per request"
    )
    avg_query_time_ms: Optional[float] = Field(
        0.0, description="Average DB query time in ms"
    )


class SystemMetrics(BaseModel):
    """System health metrics."""

    cpu_percent: float = Field(..., description="CPU usage percentage")
    memory_percent: float = Field(..., description="Memory usage percentage")
    memory_used_mb: float = Field(..., description="Memory used in MB")
    memory_total_mb: float = Field(..., description="Total memory in MB")
    disk_percent: float = Field(..., description="Disk usage percentage")
    process_memory_mb: float = Field(..., description="Process RSS memory in MB")
    thread_count: int = Field(..., description="Number of active threads")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")


class AdminDashboardResponse(BaseModel):
    """Admin dashboard data."""

    model_config = ConfigDict(from_attributes=True)

    tickets: Dict[str, int] = Field(..., description="Ticket counts by status")
    telemetry: List[TelemetryEndpointStat] = Field(
        ..., description="Top telemetry stats"
    )
    active_users: int = Field(0, description="Active users in last 24h")
    total_users: int = Field(0, description="Total registered users")
    db_status: str = Field("healthy", description="Database connection health")
    system: Optional[SystemMetrics] = Field(None, description="System health metrics")
    server_version: str = Field(..., description="Current server version string")


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

    pending: int = Field(0, description="Pending reports")
    blocked: int = Field(0, description="Blocked reports")
    cleared: int = Field(0, description="Cleared reports")
    total: int = Field(0, description="Total reports")


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


class AdminChangePasswordRequest(BaseModel):
    """Request to change admin password."""

    current_password: str = Field(...)
    new_password: str = Field(..., min_length=12)


class AdminSecurityStatusResponse(BaseModel):
    """Admin account security posture."""

    model_config = ConfigDict(from_attributes=True)

    admin_id: str
    username: str
    email: Optional[str]
    created_at: int
    last_login: Optional[int]
    otp_required: bool
    otp_enabled: bool
    must_setup_otp: bool
    backup_codes_remaining: int


class AdminOTPSetupBeginRequest(BaseModel):
    """Begin an admin OTP setup/reset flow."""

    current_password: str = Field(...)


class AdminOTPDisableRequest(BaseModel):
    """Disable admin OTP after verifying password and OTP."""

    current_password: str = Field(...)
    code: str = Field(..., min_length=6, max_length=16)


class AdminBackupCodesResponse(BaseModel):
    """Backup code regeneration response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool
    backup_codes: List[str]


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
    reason: Optional[str] = Field(
        None, max_length=500, description="Reason for blocking"
    )
    duration_hours: Optional[int] = Field(None, description="Duration in hours")


class BlockedIPResponse(BaseModel):
    """Blocked IP information."""

    ip_address: str
    reason: Optional[str]
    blocked_at: int
    blocked_by: Optional[int]
    expires_at: Optional[int]


class AccessTokenCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    token: Optional[str] = Field(None, min_length=32, max_length=128)
    expires_at: Optional[int] = Field(None, description="Unix timestamp when token expires")
    scope_mode: str = Field(
        "none", pattern="^(none|monitor|enforce)$", description="IP scope enforcement mode"
    )


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: Optional[str]
    description: Optional[str]
    created_by: Optional[str]
    created_at: int
    first_used_at: Optional[int]
    last_used_at: Optional[int]
    last_used_ip_address: Optional[str]
    last_used_user_agent: Optional[str]
    last_used_path: Optional[str]
    expires_at: Optional[int]
    scope_mode: str
    use_count_total: int
    distinct_ip_count: int
    denied_count_total: int
    revoked: bool
    revoked_at: Optional[int]
    revoked_by: Optional[str]


class AccessTokenCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token: str
    access_token: AccessTokenResponse


class AccessTokenUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    expires_at: Optional[int] = Field(None)
    clear_expiry: bool = Field(False)
    scope_mode: Optional[str] = Field(
        None, pattern="^(none|monitor|enforce)$", description="IP scope enforcement mode"
    )


class AccessTokenRotateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token: Optional[str] = Field(None, min_length=32, max_length=128)


class AccessTokenScopeCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope_type: str = Field(..., pattern="^(ip|cidr)$")
    value: str = Field(..., min_length=1, max_length=128)


class AccessTokenScopeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope_type: str
    value: str
    created_by: Optional[str]
    created_at: int


class AccessTokenUsageIPResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ip_address: str
    request_count: int
    denied_count: int
    last_seen_at: Optional[int]


class AccessTokenUsagePathResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: Optional[str]
    path: Optional[str]
    request_count: int
    last_seen_at: Optional[int]


class AccessTokenUsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    used_at: int
    ip_address: Optional[str]
    method: Optional[str]
    path: Optional[str]
    user_agent: Optional[str]
    allowed: bool
    scope_match: Optional[bool]
    reject_reason: Optional[str]


class AccessTokenDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: AccessTokenResponse
    scopes: List[AccessTokenScopeResponse]
    recent_events: List[AccessTokenUsageEventResponse]
    top_ips: List[AccessTokenUsageIPResponse]
    top_paths: List[AccessTokenUsagePathResponse]
    total_events: int
    distinct_ip_count: int
    denied_count_total: int


class ScheduledDeletionResponse(BaseModel):
    """Information about a scheduled account deletion."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    scheduled_at: int = Field(..., description="Timestamp when deletion was scheduled")
    deletion_at: int = Field(..., description="Timestamp when permanent purge will occur")
    days_left: int = Field(..., description="Approximate days remaining until purge")


class ScheduledDeletionListResponse(BaseModel):
    """List of scheduled deletions."""

    model_config = ConfigDict(from_attributes=True)

    deletions: List[ScheduledDeletionResponse] = Field(..., description="Scheduled deletions")


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


class LogFileInfo(BaseModel):
    """Metadata for a log file."""

    filename: str
    size: int
    modified: int
    is_zipped: bool


class LogLine(BaseModel):
    """A single log entry."""

    timestamp: str
    level: str
    message: str
    raw: str


class LogViewResponse(BaseModel):
    """Log file content with pagination."""

    filename: str
    total_lines: int
    lines: List[LogLine]
    limit: int
    offset: int


class AutomodRuleAction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_type: str
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    notify_user: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutomodRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str
    name: str
    rule_type: str
    enabled: bool
    config: Dict[str, Any]
    actions: List[AutomodRuleAction]
    exempt_roles: List[str]
    exempt_channels: List[str]
    priority: int
    check_all: bool
    created_at: int
    updated_at: int
    created_by: str


class AutomodRuleCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: int
    name: str = Field(..., min_length=1, max_length=100)
    rule_type: str
    config: Dict[str, Any]
    actions: List[AutomodRuleAction]
    exempt_roles: Optional[List[int]] = None
    exempt_channels: Optional[List[int]] = None
    priority: Optional[int] = 0
    check_all: Optional[bool] = False
    enabled: Optional[bool] = True


class AutomodRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    actions: Optional[List[AutomodRuleAction]] = None
    exempt_roles: Optional[List[int]] = None
    exempt_channels: Optional[List[int]] = None
    priority: Optional[int] = None
    check_all: Optional[bool] = None
    enabled: Optional[bool] = None


class AutomodConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    ai: Dict[str, Any] = Field(default_factory=dict)


class AutomodConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: Optional[bool] = None
    ai: Optional[Dict[str, Any]] = None
