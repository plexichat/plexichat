"""
Authentication data models.

All models are dataclasses for clean, immutable data structures.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum
from src.core.base import SnowflakeID


class AccountType(Enum):
    """Type of account."""

    USER = "user"
    BOT = "bot"
    SYSTEM = "system"


class AuthStatus(Enum):
    """Authentication result status."""

    SUCCESS = "success"
    TWO_FACTOR_REQUIRED = "2fa_required"
    VERIFICATION_REQUIRED = "verification_required"
    FAILED = "failed"


class AuditEventType(Enum):
    """Types of audit events."""

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    REGISTER = "register"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFIED = "email_verified"
    TWO_FACTOR_ENABLED = "2fa_enabled"
    TWO_FACTOR_DISABLED = "2fa_disabled"
    TWO_FACTOR_BACKUP_USED = "2fa_backup_used"
    TWO_FACTOR_BACKUP_REGENERATED = "2fa_backup_regenerated"
    SESSION_REVOKED = "session_revoked"
    DEVICE_REVOKED = "device_revoked"
    BOT_CREATED = "bot_created"
    BOT_DELETED = "bot_deleted"
    BOT_TOKEN_REGENERATED = "bot_token_regenerated"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    PERMISSIONS_CHANGED = "permissions_changed"
    OAUTH_LOGIN = "oauth_login"
    OAUTH_LINK = "oauth_link"


@dataclass
class User:
    """User account model."""

    id: SnowflakeID
    account_type: AccountType
    username: str
    email: Optional[str]
    permissions: Dict[str, bool]
    created_at: int
    updated_at: int
    email_verified: bool = False
    account_locked: bool = False
    force_username_change: bool = False
    locked_until: Optional[int] = None
    failed_login_attempts: int = 0
    last_login_at: Optional[int] = None
    totp_enabled: bool = False
    public_key: Optional[bytes] = None
    age_verified: bool = False
    date_of_birth: Optional[str] = None # ISO format YYYY-MM-DD

    @property
    def avatar_url(self) -> Optional[str]:
        """Get the avatar URL for this user."""
        return f"/api/v1/avatars/users/{self.id}"

    # Not stored, only set on specific operations
    password_hash: Optional[str] = field(default=None, repr=False)
    totp_secret_encrypted: Optional[str] = field(default=None, repr=False)
    backup_codes_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class Session:
    """User session model."""

    id: SnowflakeID
    user_id: SnowflakeID
    device_id: Optional[SnowflakeID]
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: int
    expires_at: int
    last_activity: int
    revoked: bool = False

    # Not stored, only set on creation
    token: Optional[str] = field(default=None, repr=False)
    token_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class Bot:
    """Bot account model."""

    id: SnowflakeID
    owner_id: SnowflakeID
    username: str
    display_name: str
    permissions: Dict[str, bool]
    created_at: int
    disabled: bool = False

    # Not stored, only set on creation/regeneration
    token: Optional[str] = field(default=None, repr=False)
    token_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class Device:
    """Known device model."""

    id: SnowflakeID
    user_id: SnowflakeID
    fingerprint: str
    name: Optional[str]
    device_type: Optional[str]
    first_seen_at: int
    last_seen_at: int


@dataclass
class KnownIP:
    """Known IP address model."""

    id: SnowflakeID
    user_id: SnowflakeID
    ip_address: str
    first_seen_at: int
    last_seen_at: int


@dataclass
class AuditEntry:
    """Audit log entry model."""

    id: SnowflakeID
    user_id: Optional[SnowflakeID]
    event_type: AuditEventType
    ip_address: Optional[str]
    device_id: Optional[SnowflakeID]
    timestamp: int
    details: Optional[Dict[str, Any]]
    success: bool


@dataclass
class TokenInfo:
    """Information about a verified token."""

    valid: bool
    token_type: str  # "user" or "bot"
    account_id: SnowflakeID  # User ID or Bot ID
    user_id: SnowflakeID  # Always the human user (for bots, the owner)
    session_id: Optional[SnowflakeID]  # Only for user tokens
    permissions: Dict[str, bool]
    rate_limit_tier: str
    expires_at: Optional[int]
    username: str
    account_type: AccountType
    avatar_url: Optional[str] = None


@dataclass
class AuthResult:
    """Result of an authentication attempt."""

    status: AuthStatus
    token: Optional[str] = None
    user: Optional[User] = None
    session: Optional[Session] = None
    challenge_token: Optional[str] = None
    methods: Optional[List[str]] = None
    expires_in: Optional[int] = None
    message: Optional[str] = None


@dataclass
class TwoFactorSetup:
    """2FA setup information."""

    secret: str
    qr_uri: str
    backup_codes: List[str]
    issuer: str
    username: str


@dataclass
class TwoFactorStatus:
    """2FA status for a user."""

    enabled: bool
    backup_codes_remaining: int
    last_used: Optional[int] = None


@dataclass
class PasswordValidation:
    """Password validation result."""

    valid: bool
    score: int  # 0-5
    issues: List[str]


@dataclass
class EmailToken:
    """Email verification/reset token model."""

    id: SnowflakeID
    user_id: SnowflakeID
    token_type: str  # 'verify_email' or 'reset_password'
    created_at: int
    expires_at: int
    used: bool = False

    # Not stored
    token: Optional[str] = field(default=None, repr=False)
    token_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class TwoFactorChallenge:
    """Temporary 2FA challenge during login."""

    id: SnowflakeID
    user_id: SnowflakeID
    created_at: int
    expires_at: int
    device_id: Optional[SnowflakeID]
    ip_address: Optional[str]
    user_agent: Optional[str]
    used: bool = False

    # Not stored
    token: Optional[str] = field(default=None, repr=False)
    token_hash: Optional[str] = field(default=None, repr=False)
