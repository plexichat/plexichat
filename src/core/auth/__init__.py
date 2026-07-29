"""
Authentication module - Secure authentication for Plexichat API.

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

from typing import Optional, Protocol

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

# Re-export all public functions from submodules
from .registration import (
    register,
    register_selftest,
    verify_email,
    resend_verification,
)
from .login import (
    login,
    complete_2fa,
)
from .sessions import (
    verify_token,
    refresh_session,
    create_session_for_user,
    logout,
    logout_all,
    logout_all_users,
    get_sessions,
    revoke_session,
    schedule_account_deletion,
    cancel_account_deletion,
    delay_account_deletion,
    force_purge_account,
)
from .profile import (
    update_user,
)
from .twofa import (
    setup_2fa,
    confirm_2fa,
    disable_2fa,
    regenerate_backup_codes,
    get_2fa_status,
)
from .passkeys import (
    generate_passkey_registration_options,
    verify_passkey_registration,
    generate_passkey_authentication_options,
    verify_passkey_authentication,
    list_passkeys,
    revoke_passkey,
    rename_passkey,
)
from .passwords import (
    change_password,
    request_password_reset,
    reset_password,
    validate_password,
)
from .bots import (
    create_bot,
    get_bot,
    get_user_bots,
    regenerate_bot_token,
    update_bot_permissions,
    disable_bot,
    enable_bot,
    delete_bot,
)
from .tokens import (
    create_api_access_token,
    list_api_access_tokens,
    get_api_access_token,
    update_api_access_token,
    revoke_api_access_token,
    unrevoke_api_access_token,
    rotate_api_access_token,
    add_api_access_token_scope,
    remove_api_access_token_scope,
    list_api_access_token_scopes,
    get_api_access_token_usage,
    verify_api_access_token,
    is_api_access_token_required,
)
from .devices import (
    get_devices,
    rename_device,
    revoke_device,
)
from .ip_blacklist import (
    block_ip,
    unblock_ip,
    is_ip_blocked,
    get_blocked_ips,
)
from .audit import (
    get_login_history,
    get_security_events,
)
from .users import (
    get_user,
    get_user_by_username,
    get_users_bulk,
    get_user_profiles_bulk,
    grant_permission,
)
from .capabilities import (
    has_capability,
    require_capability,
)
from .oauth import (
    oauth_login,
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
    "AccessToken",
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
    "register_selftest",
    "verify_email",
    "resend_verification",
    # Login
    "login",
    "complete_2fa",
    "oauth_login",
    # Sessions
    "verify_token",
    "refresh_session",
    "create_session_for_user",
    "logout",
    "logout_all",
    "logout_all_users",
    "get_sessions",
    "revoke_session",
    "schedule_account_deletion",
    "cancel_account_deletion",
    "delay_account_deletion",
    "force_purge_account",
    # 2FA
    "setup_2fa",
    "confirm_2fa",
    "disable_2fa",
    "regenerate_backup_codes",
    "get_2fa_status",
    # Passkeys
    "generate_passkey_registration_options",
    "verify_passkey_registration",
    "generate_passkey_authentication_options",
    "verify_passkey_authentication",
    "list_passkeys",
    "revoke_passkey",
    "rename_passkey",
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
    "unrevoke_api_access_token",
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
    # IP Blacklist
    "block_ip",
    "unblock_ip",
    "is_ip_blocked",
    "get_blocked_ips",
    # Audit
    "get_login_history",
    "get_security_events",
    # Utility
    "get_user",
    "get_user_by_username",
    "get_users_bulk",
    "get_user_profiles_bulk",
    "grant_permission",
    "has_capability",
    "require_capability",
    "update_user",
]
