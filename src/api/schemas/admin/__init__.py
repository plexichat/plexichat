"""
Admin API schemas sub-package.

Split from monolithic admin.py into domain-specific modules.
All schemas are re-exported here for backward compatibility.
"""

from .admin_auth import (
    AdminLoginRequest,
    AdminLoginResponse,
    OTPVerifyRequest,
    AdminChangePasswordRequest,
    AdminSecurityStatusResponse,
    AdminOTPSetupBeginRequest,
    AdminOTPDisableRequest,
    AdminBackupCodesResponse,
)
from .tickets import (
    TicketStatusUpdate,
    InternalNoteCreate,
    TicketResponse,
    NoteResponse,
)
from .telemetry import (
    TelemetryEndpointStat,
    SystemMetrics,
    AdminDashboardResponse,
    TelemetryStatsResponse,
    TelemetryHistoryBucket,
    TelemetryHistoryResponse,
    TelemetryResetResponse,
    TelemetryExportResponse,
)
from .hash_reports import (
    HashReportReviewRequest,
    ManualBlockHashRequest,
    BlockedHashResponse,
    HashReportResponse,
    HashReportCountsResponse,
    HashReportReviewResponse,
    BlockHashResponse,
)
from .moderation_reports import (
    ModerationReportCountsResponse,
    ModerationReportReviewRequest,
    ModerationReportReviewResponse,
    MessageReportResponse,
    UserReportResponse,
    BlockUserRequest,
    BlockedUserResponse,
    BlockUserResponse,
)
from .user_management import (
    UserTierUpdate,
    UserSearchResponse,
    UserSearchListResponse,
    UserDetailsResponse,
    BannedUsernameResponse,
    BannedUsernameCreate,
    UserNotesResponse,
    UserNotesUpdate,
    UserTierUpdateResponse,
    UserBadgeUpdateResponse,
    ScheduledDeletionResponse,
    ScheduledDeletionListResponse,
    ForceLogoutRequest,
    ForceUsernameChangeRequest,
    UserLockRequest,
    AvailableTierInfo,
    AvailableTiersResponse,
    AvailableBadgesResponse,
)
from .ip_management import (
    IPBlockRequest,
    BlockedIPResponse,
)
from .access_tokens import (
    AccessTokenCreateRequest,
    AccessTokenResponse,
    AccessTokenCreateResponse,
    AccessTokenUpdateRequest,
    AccessTokenRotateRequest,
    AccessTokenScopeCreateRequest,
    AccessTokenScopeResponse,
    AccessTokenUsageIPResponse,
    AccessTokenUsagePathResponse,
    AccessTokenUsageEventResponse,
    AccessTokenDetailResponse,
)
from .logs import (
    LogFileInfo,
    LogLine,
    LogViewResponse,
)
from .automod import (
    AutomodRuleAction,
    AutomodRuleResponse,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
    AutomodConfigResponse,
    AutomodConfigUpdateRequest,
)

__all__ = [
    # Admin Auth
    "AdminLoginRequest",
    "AdminLoginResponse",
    "OTPVerifyRequest",
    "AdminChangePasswordRequest",
    "AdminSecurityStatusResponse",
    "AdminOTPSetupBeginRequest",
    "AdminOTPDisableRequest",
    "AdminBackupCodesResponse",
    # Tickets
    "TicketStatusUpdate",
    "InternalNoteCreate",
    "TicketResponse",
    "NoteResponse",
    # Telemetry
    "TelemetryEndpointStat",
    "SystemMetrics",
    "AdminDashboardResponse",
    "TelemetryStatsResponse",
    "TelemetryHistoryBucket",
    "TelemetryHistoryResponse",
    "TelemetryResetResponse",
    "TelemetryExportResponse",
    # Hash Reports
    "HashReportReviewRequest",
    "ManualBlockHashRequest",
    "BlockedHashResponse",
    "HashReportResponse",
    "HashReportCountsResponse",
    "HashReportReviewResponse",
    "BlockHashResponse",
    # Moderation Reports
    "ModerationReportCountsResponse",
    "ModerationReportReviewRequest",
    "ModerationReportReviewResponse",
    "MessageReportResponse",
    "UserReportResponse",
    "BlockUserRequest",
    "BlockedUserResponse",
    "BlockUserResponse",
    # User Management
    "UserTierUpdate",
    "UserSearchResponse",
    "UserSearchListResponse",
    "UserDetailsResponse",
    "BannedUsernameResponse",
    "BannedUsernameCreate",
    "UserNotesResponse",
    "UserNotesUpdate",
    "UserTierUpdateResponse",
    "UserBadgeUpdateResponse",
    "ScheduledDeletionResponse",
    "ScheduledDeletionListResponse",
    "ForceLogoutRequest",
    "ForceUsernameChangeRequest",
    "UserLockRequest",
    "AvailableTierInfo",
    "AvailableTiersResponse",
    "AvailableBadgesResponse",
    # IP Management
    "IPBlockRequest",
    "BlockedIPResponse",
    # Access Tokens
    "AccessTokenCreateRequest",
    "AccessTokenResponse",
    "AccessTokenCreateResponse",
    "AccessTokenUpdateRequest",
    "AccessTokenRotateRequest",
    "AccessTokenScopeCreateRequest",
    "AccessTokenScopeResponse",
    "AccessTokenUsageIPResponse",
    "AccessTokenUsagePathResponse",
    "AccessTokenUsageEventResponse",
    "AccessTokenDetailResponse",
    # Logs
    "LogFileInfo",
    "LogLine",
    "LogViewResponse",
    # Automod
    "AutomodRuleAction",
    "AutomodRuleResponse",
    "AutomodRuleCreateRequest",
    "AutomodRuleUpdateRequest",
    "AutomodConfigResponse",
    "AutomodConfigUpdateRequest",
]
