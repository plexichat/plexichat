"""
Session management module.

This module handles session token verification, refresh, logout operations,
session listing, revocation, and account deletion scheduling.
"""

from typing import Optional, Dict, List
from .models import TokenInfo, Session, AuthResult
from ._lazy import _get_auth_manager


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
    return (
        _get_auth_manager()
        .get_instance()
        .verify_token(token, ip_address, user_agent, is_selftest)
    )


def refresh_session(token: str) -> Optional[str]:
    """Refresh a session token. Returns new token or None if not refreshable."""
    return _get_auth_manager().get_instance().refresh_session(token)


def create_session_for_user(
    user_id: int,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuthResult:
    """Create a session for an already-authenticated user (bypasses password re-verify)."""
    return (
        _get_auth_manager()
        .get_instance()
        .create_session_for_user(user_id, device_info, ip_address, user_agent)
    )


def logout(token: str) -> bool:
    """Logout and invalidate a session token."""
    return _get_auth_manager().get_instance().logout(token)


def logout_all(user_id: int, except_token: Optional[str] = None) -> int:
    """Logout all sessions for a user. Returns count of sessions revoked."""
    return _get_auth_manager().get_instance().logout_all(user_id, except_token)


def logout_all_users() -> int:
    """Logout all sessions for all users."""
    return _get_auth_manager().get_instance().logout_all_users()


def get_sessions(user_id: int) -> List[Session]:
    """Get all active sessions for a user."""
    return _get_auth_manager().get_instance().get_sessions(user_id)


def revoke_session(user_id: int, session_id: int) -> bool:
    """Revoke a specific session."""
    return _get_auth_manager().get_instance().revoke_session(user_id, session_id)


def schedule_account_deletion(
    user_id: int, password: str, totp_code: Optional[str] = None
) -> bool:
    """Schedule an account for deletion."""
    return (
        _get_auth_manager()
        .get_instance()
        .schedule_account_deletion(user_id, password, totp_code)
    )


def cancel_account_deletion(user_id: int, admin_id: Optional[int] = None) -> bool:
    """Cancel a scheduled account deletion."""
    return _get_auth_manager().get_instance().cancel_account_deletion(user_id, admin_id)


def delay_account_deletion(
    user_id: int, additional_days: int, admin_id: Optional[int] = None
) -> bool:
    """Extend the deletion grace period for a scheduled account deletion."""
    return (
        _get_auth_manager()
        .get_instance()
        .delay_account_deletion(user_id, additional_days, admin_id)
    )


def force_purge_account(user_id: int, admin_id: Optional[int] = None) -> bool:
    """Immediately purge a user account, bypassing the grace period."""
    return _get_auth_manager().get_instance().force_purge_account(user_id, admin_id)


__all__ = [
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
]
