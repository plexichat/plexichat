"""
Authentication module - Secure authentication for PlexiChat API.

This module provides:
- User registration and login
- Secure session tokens (not JWT)
- Two-factor authentication (TOTP)
- Bot account management
- Device and IP tracking
- Granular permissions
- Audit logging

Usage:
    # In main.py (setup once)
    from src.core.auth import setup
    from src.core.database import Database

    db = Database()
    db.connect()
    setup(db)

    # In any other file
    from src.core import auth

    user = auth.register("username", "email@example.com", "password")
    result = auth.login("username", "password")
    token_info = auth.verify_token(result.token)
"""

from typing import Optional, List, Dict, Protocol, Any

from .exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    EmailNotVerifiedError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorRequiredError,
    TwoFactorInvalidError,
    PermissionDeniedError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
    BotLimitExceededError,
    InvalidUsernameError,
    InvalidEmailError,
)
from .models import (
    User,
    Session,
    Bot,
    AccessToken,
    Device,
    KnownIP,
    AuditEntry,
    TokenInfo,
    AuthResult,
    TwoFactorSetup,
    TwoFactorStatus,
    PasswordValidation,
    AccountType,
    AuthStatus,
    AuditEventType,
)
from .permissions import (
    PERMISSIONS,
    DEFAULT_USER_PERMISSIONS,
    DEFAULT_BOT_PERMISSIONS,
    has_permission,
    validate_permissions,
)

# Module state
_manager = None
_setup_complete = False


class EmailSender(Protocol):
    """Protocol for email sending. Implement this to enable email features."""

    def send(self, to: str, subject: str, body: str, html: bool = False) -> bool:
        """Send an email. Returns True if successful."""
        ...


def setup(db, email_sender: Optional[EmailSender] = None) -> None:
    """
    Initialize the authentication module.

    Args:
        db: Database instance (must be connected)
        email_sender: Optional email sender for verification emails
    """
    global _manager, _setup_complete

    from .manager import AuthManager
    from .oauth import state as oauth_state

    _manager = AuthManager(db, email_sender)

    # Initialize OAuth state manager for secure OAuth flows
    # Get OAuth config for state TTL settings
    try:
        import utils.config as config_util

        oauth_config = config_util.get("oauth", {}) if config_util else {}
    except ImportError:
        oauth_config = {}

    oauth_state.setup(db, oauth_config)

    _setup_complete = True


def _get_manager():
    """Get the auth manager, ensuring setup was called."""
    if not _setup_complete:
        raise RuntimeError("Auth not initialized. Call auth.setup(db) first.")
    assert _manager is not None
    return _manager


# === User Registration ===


def register(
    username: str,
    email: str,
    password: str,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    age: Optional[int] = None,
    dob: Optional[str] = None,
) -> User:
    """
    Register a new user account.

    Args:
        username: Unique username
        email: Email address
        password: Password (will be validated for strength)
        device_info: Optional device information
        ip_address: Optional IP address
        age: Optional user age
        dob: Optional date of birth

    Returns:
        Created User object

    Raises:
        UserExistsError: Username or email already taken
        WeakPasswordError: Password does not meet requirements
        InvalidUsernameError: Username format invalid
        InvalidEmailError: Email format invalid
    """
    return _get_manager().register(
        username, email, password, device_info, ip_address, age, dob
    )


def verify_email(token: str) -> bool:
    """Verify email address with token from verification email."""
    return _get_manager().verify_email(token)


def resend_verification(email: str) -> bool:
    """Resend email verification. Returns False if email not configured."""
    return _get_manager().resend_verification(email)


# === User Login ===


def login(
    username: str,
    password: str,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuthResult:
    """
    Authenticate a user.

    Args:
        username: Username or email
        password: Password
        device_info: Optional device information
        ip_address: Optional IP address
        user_agent: Optional user agent string

    Returns:
        AuthResult with status and token/challenge

    Raises:
        InvalidCredentialsError: Wrong username or password
        AccountLockedError: Account temporarily locked
        AccountDisabledError: Account permanently disabled
        EmailNotVerifiedError: Email verification required
    """
    return _get_manager().login(username, password, device_info, ip_address, user_agent)


def complete_2fa(challenge_token: str, code: str) -> AuthResult:
    """Complete 2FA challenge with TOTP code or backup code."""
    return _get_manager().complete_2fa(challenge_token, code)


# === Session Management ===


def verify_token(
    token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    is_selftest: bool = False,
) -> TokenInfo:
    """
    Verify a session or bot token.

    Args:
        token: The token to verify
        ip_address: Optional IP for tracking
        user_agent: Optional user agent for binding
        is_selftest: Whether this is an internal self-test request

    Returns:
        TokenInfo with user/bot details and permissions

    Raises:
        TokenInvalidError: Token is malformed or invalid
        TokenExpiredError: Token has expired
    """
    return _get_manager().verify_token(token, ip_address, user_agent, is_selftest)


def refresh_session(token: str) -> Optional[str]:
    """Refresh a session token. Returns new token or None if not refreshable."""
    return _get_manager().refresh_session(token)


def logout(token: str) -> bool:
    """Logout and invalidate a session token."""
    return _get_manager().logout(token)


def logout_all(user_id: int, except_token: Optional[str] = None) -> int:
    """Logout all sessions for a user. Returns count of sessions revoked."""
    return _get_manager().logout_all(user_id, except_token)


def logout_all_users() -> int:
    """Logout all sessions for all users."""
    return _get_manager().logout_all_users()


def get_sessions(user_id: int) -> List[Session]:
    """Get all active sessions for a user."""
    return _get_manager().get_sessions(user_id)


def revoke_session(user_id: int, session_id: int) -> bool:
    """Revoke a specific session."""
    return _get_manager().revoke_session(user_id, session_id)


# === User Profile ===


def update_user(
    user_id: int,
    username: Optional[str] = None,
    email: Optional[str] = None,
    permissions: Optional[Dict[str, bool]] = None,
) -> User:
    """
    Update user profile information.

    Args:
        user_id: ID of the user to update
        username: New username (optional)
        email: New email address (optional)
        permissions: New permissions (optional)

    Returns:
        Updated User object
    """
    return _get_manager().update_user(user_id, username, email, permissions)


# === Two-Factor Authentication ===


def setup_2fa(user_id: int) -> TwoFactorSetup:
    """
    Begin 2FA setup. Returns secret, QR URI, and backup codes.
    User must call confirm_2fa() with a valid code to enable.
    """
    return _get_manager().setup_2fa(user_id)


def confirm_2fa(user_id: int, code: str) -> bool:
    """Confirm 2FA setup with a valid TOTP code."""
    return _get_manager().confirm_2fa(user_id, code)


def disable_2fa(user_id: int, password: str, code: str) -> bool:
    """Disable 2FA. Requires password and current TOTP code."""
    return _get_manager().disable_2fa(user_id, password, code)


def regenerate_backup_codes(user_id: int, password: str) -> List[str]:
    """Regenerate backup codes. Invalidates old codes."""
    return _get_manager().regenerate_backup_codes(user_id, password)


def get_2fa_status(user_id: int) -> TwoFactorStatus:
    """Get 2FA status for a user."""
    return _get_manager().get_2fa_status(user_id)


# === Password Management ===


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change password. Requires current password."""
    return _get_manager().change_password(user_id, old_password, new_password)


def request_password_reset(email: str) -> bool:
    """Request password reset email. Returns False if email not configured."""
    return _get_manager().request_password_reset(email)


def reset_password(token: str, new_password: str) -> bool:
    """Reset password with token from reset email."""
    return _get_manager().reset_password(token, new_password)


def validate_password(password: str) -> PasswordValidation:
    """Validate password strength without creating account."""
    return _get_manager().validate_password(password)


# === Bot Management ===


def create_bot(
    owner_id: int,
    username: str,
    display_name: str,
    permissions: Optional[Dict[str, bool]] = None,
) -> Bot:
    """
    Create a bot account.

    Args:
        owner_id: User ID of the bot owner
        username: Unique username for the bot
        display_name: Display name
        permissions: Optional custom permissions (defaults applied if None)

    Returns:
        Bot object with token (token only returned on creation)
    """
    return _get_manager().create_bot(owner_id, username, display_name, permissions)


def get_bot(bot_id: int) -> Optional[Bot]:
    """Get a bot by ID."""
    return _get_manager().get_bot(bot_id)


def get_user_bots(owner_id: int) -> List[Bot]:
    """Get all bots owned by a user."""
    return _get_manager().get_user_bots(owner_id)


def regenerate_bot_token(owner_id: int, bot_id: int) -> str:
    """Regenerate bot token. Old token immediately invalid."""
    return _get_manager().regenerate_bot_token(owner_id, bot_id)


def update_bot_permissions(
    owner_id: int, bot_id: int, permissions: Dict[str, bool]
) -> Bot:
    """Update bot permissions."""
    return _get_manager().update_bot_permissions(owner_id, bot_id, permissions)


def disable_bot(owner_id: int, bot_id: int) -> bool:
    """Disable a bot (can be re-enabled)."""
    return _get_manager().disable_bot(owner_id, bot_id)


def enable_bot(owner_id: int, bot_id: int) -> bool:
    """Re-enable a disabled bot."""
    return _get_manager().enable_bot(owner_id, bot_id)


def delete_bot(owner_id: int, bot_id: int) -> bool:
    """Permanently delete a bot."""
    return _get_manager().delete_bot(owner_id, bot_id)


# === API Access Tokens ===


def create_api_access_token(
    name: Optional[str],
    created_by: Optional[int],
    token_value: Optional[str] = None,
    description: Optional[str] = None,
    expires_at: Optional[int] = None,
    scope_mode: str = "none",
) -> AccessToken:
    return _get_manager().create_api_access_token(
        name,
        created_by,
        token_value,
        description=description,
        expires_at=expires_at,
        scope_mode=scope_mode,
    )


def list_api_access_tokens(include_revoked: bool = True) -> List[AccessToken]:
    return _get_manager().list_api_access_tokens(include_revoked)


def get_api_access_token(token_id: int) -> Optional[AccessToken]:
    return _get_manager().get_api_access_token(token_id)


def update_api_access_token(
    token_id: int,
    updated_by: Optional[int],
    name: Optional[str] = None,
    description: Optional[str] = None,
    expires_at: Optional[int] = None,
    clear_expiry: bool = False,
    scope_mode: Optional[str] = None,
) -> Optional[AccessToken]:
    return _get_manager().update_api_access_token(
        token_id,
        updated_by,
        name=name,
        description=description,
        expires_at=expires_at,
        clear_expiry=clear_expiry,
        scope_mode=scope_mode,
    )


def revoke_api_access_token(token_id: int, revoked_by: Optional[int]) -> bool:
    return _get_manager().revoke_api_access_token(token_id, revoked_by)


def rotate_api_access_token(
    token_id: int,
    rotated_by: Optional[int],
    token_value: Optional[str] = None,
) -> Optional[AccessToken]:
    return _get_manager().rotate_api_access_token(token_id, rotated_by, token_value)


def add_api_access_token_scope(
    token_id: int,
    scope_type: str,
    value: str,
    created_by: Optional[int],
) -> Dict[str, Any]:
    return _get_manager().add_api_access_token_scope(
        token_id, scope_type, value, created_by
    )


def remove_api_access_token_scope(token_id: int, scope_id: int) -> bool:
    return _get_manager().remove_api_access_token_scope(token_id, scope_id)


def list_api_access_token_scopes(token_id: int) -> List[Dict[str, Any]]:
    return _get_manager().list_api_access_token_scopes(token_id)


def get_api_access_token_usage(
    token_id: int,
    recent_limit: int = 100,
) -> Dict[str, Any]:
    return _get_manager().get_api_access_token_usage(token_id, recent_limit)


def verify_api_access_token(
    token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
) -> bool:
    return _get_manager().verify_api_access_token(
        token,
        ip_address=ip_address,
        user_agent=user_agent,
        path=path,
        method=method,
    )


def is_api_access_token_required() -> bool:
    return _get_manager().is_api_access_token_required()


# === Device Management ===


def get_devices(user_id: int) -> List[Device]:
    """Get all known devices for a user."""
    return _get_manager().get_devices(user_id)


def rename_device(user_id: int, device_id: int, name: str) -> bool:
    """Rename a device."""
    return _get_manager().rename_device(user_id, device_id, name)


def revoke_device(user_id: int, device_id: int) -> bool:
    """Revoke a device and all its sessions."""
    return _get_manager().revoke_device(user_id, device_id)


# === IP Blacklisting ===


def block_ip(
    ip_address: str,
    reason: Optional[str] = None,
    blocked_by: Optional[int] = None,
    duration_hours: Optional[int] = None,
) -> bool:
    """Block an IP address."""
    return _get_manager().block_ip(ip_address, reason, blocked_by, duration_hours)


def unblock_ip(ip_address: str) -> bool:
    """Unblock an IP address."""
    return _get_manager().unblock_ip(ip_address)


def is_ip_blocked(ip_address: str) -> bool:
    """Check if an IP address is blocked."""
    return _get_manager().is_ip_blocked(ip_address)


def get_blocked_ips() -> List[Dict[str, Any]]:
    """Get all blocked IPs."""
    return _get_manager().get_blocked_ips()


# === Audit ===


def get_login_history(user_id: int, limit: int = 50) -> List[AuditEntry]:
    """Get login history for a user."""
    return _get_manager().get_login_history(user_id, limit)


def get_security_events(user_id: int, limit: int = 50) -> List[AuditEntry]:
    """Get security events for a user."""
    return _get_manager().get_security_events(user_id, limit)


# === Utility ===


def get_user(user_id: int) -> Optional[User]:
    """Get a user by ID."""
    return _get_manager().get_user(user_id)


def get_user_by_username(username: str) -> Optional[User]:
    """Get a user by username."""
    return _get_manager().get_user_by_username(username)


def get_users_bulk(user_ids: List[int]) -> Dict[int, User]:
    """Get multiple users by ID in a single query."""
    return _get_manager().get_users_bulk(user_ids)


def get_user_profiles_bulk(user_ids: List[int]) -> Dict[str, Any]:
    """Get multiple user profiles in a single query (cached)."""
    return _get_manager().get_user_profiles_bulk(user_ids)


def grant_permission(user_id: int, permission: str) -> bool:
    """Grant a specific permission to a user."""
    return _get_manager().grant_permission(user_id, permission)


def has_capability(token_info: TokenInfo, capability: str) -> bool:
    """Check if token has a specific capability/permission."""
    return has_permission(token_info.permissions, capability)


def require_capability(token_info: TokenInfo, capability: str) -> None:
    """Require a capability, raising PermissionDeniedError if missing."""
    if not has_permission(token_info.permissions, capability):
        raise PermissionDeniedError(f"Missing required permission: {capability}")


# === OAuth ===


def oauth_login(
    provider: str,
    external_id: str,
    email: Optional[str] = None,
    username_hint: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    age: Optional[int] = None,
    dob: Optional[str] = None,
) -> AuthResult:
    """
    Login or register via OAuth provider.

    If user exists with this OAuth link, logs them in.
    If email matches existing user, links OAuth and logs in.
    Otherwise creates new account.
    """
    return _get_manager().oauth_login(
        provider=provider,
        external_id=external_id,
        email=email,
        username_hint=username_hint,
        ip_address=ip_address,
        user_agent=user_agent,
        age=age,
        dob=dob,
    )


__all__ = [
    # Setup
    "setup",
    "EmailSender",
    # Exceptions
    "AuthError",
    "InvalidCredentialsError",
    "AccountLockedError",
    "AccountDisabledError",
    "EmailNotVerifiedError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TwoFactorRequiredError",
    "TwoFactorInvalidError",
    "PermissionDeniedError",
    "UserExistsError",
    "UserNotFoundError",
    "WeakPasswordError",
    "BotLimitExceededError",
    "InvalidUsernameError",
    "InvalidEmailError",
    # Models
    "User",
    "Session",
    "Bot",
    "Device",
    "KnownIP",
    "AuditEntry",
    "TokenInfo",
    "AuthResult",
    "TwoFactorSetup",
    "TwoFactorStatus",
    "PasswordValidation",
    "AccountType",
    "AuthStatus",
    "AuditEventType",
    # Permissions
    "PERMISSIONS",
    "DEFAULT_USER_PERMISSIONS",
    "DEFAULT_BOT_PERMISSIONS",
    "has_permission",
    "validate_permissions",
    # Registration
    "register",
    "verify_email",
    "resend_verification",
    # Login
    "login",
    "complete_2fa",
    "oauth_login",
    # Sessions
    "verify_token",
    "refresh_session",
    "logout",
    "logout_all",
    "get_sessions",
    "revoke_session",
    # 2FA
    "setup_2fa",
    "confirm_2fa",
    "disable_2fa",
    "regenerate_backup_codes",
    "get_2fa_status",
    # Password
    "change_password",
    "request_password_reset",
    "reset_password",
    "validate_password",
    # Bots
    "create_bot",
    "get_bot",
    "get_user_bots",
    "regenerate_bot_token",
    "update_bot_permissions",
    "disable_bot",
    "enable_bot",
    "delete_bot",
    # API access tokens
    "create_api_access_token",
    "list_api_access_tokens",
    "get_api_access_token",
    "update_api_access_token",
    "revoke_api_access_token",
    "rotate_api_access_token",
    "add_api_access_token_scope",
    "remove_api_access_token_scope",
    "list_api_access_token_scopes",
    "get_api_access_token_usage",
    "verify_api_access_token",
    "is_api_access_token_required",
    # Devices
    "get_devices",
    "rename_device",
    "revoke_device",
    # Audit
    "get_login_history",
    "get_security_events",
    # Utility
    "get_user",
    "get_user_by_username",
    "get_user_profiles_bulk",
    "grant_permission",
    "has_capability",
    "require_capability",
]
