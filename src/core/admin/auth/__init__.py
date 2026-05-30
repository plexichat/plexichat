"""
Backward-compatible admin auth package.

Re-exports dataclasses and module-level function wrappers that delegate
to a lazily-initialized singleton AdminAuth instance.
"""

from typing import Any, List, Optional, Tuple

from .composer import AdminAuth
from .dataclasses import AdminLoginResult, AdminSecurityStatus

_auth_instance: Optional[AdminAuth] = None


def _get_auth() -> AdminAuth:
    """Get or create the singleton AdminAuth instance."""
    global _auth_instance
    if _auth_instance is None:
        raise RuntimeError("Admin auth not initialized. Call init_auth(db) first.")
    return _auth_instance


def init_auth(db: Any) -> None:
    """Initialize the singleton AdminAuth instance with a database session."""
    global _auth_instance
    _auth_instance = AdminAuth(db)


def ensure_admin_user(db: Optional[Any] = None) -> None:
    """Ensure admin user exists, create with random password if not."""
    if db is not None:
        init_auth(db)
    _get_auth().ensure_admin_user()


def authenticate_admin(
    db: Any, username: str, password: str, ip: str = "unknown"
) -> AdminLoginResult:
    """Authenticate admin user."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().authenticate_admin(username, password, ip)


def verify_otp_setup(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify and finalize OTP setup for an administrator."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().verify_otp_setup(admin_id, code, challenge_token)


def verify_otp(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify an OTP code for an administrator login."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().verify_otp(admin_id, code, challenge_token)


def create_session(db: Any, admin_id: int, expires_hours: int = 8) -> str:
    """Create admin session."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().create_session(admin_id, expires_hours)


def validate_session(db: Any, token: str) -> Optional[int]:
    """Validate an administrator session token."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().validate_session(token)


def logout(db: Any, token: str) -> bool:
    """Invalidate an administrator session."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().logout(token)


def check_password_change_required(db: Any, admin_id: int) -> bool:
    """Check if admin is required to change password."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().check_password_change_required(admin_id)


def change_password(
    db: Any, admin_id: int, current_password: str, new_password: str
) -> Tuple[bool, str]:
    """Change the password for an administrator."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().change_password(admin_id, current_password, new_password)


def change_admin_password(
    db: Any, admin_id: int, old_password: str, new_password: str
) -> Tuple[bool, str]:
    """Change admin password with validation."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().change_admin_password(admin_id, old_password, new_password)


def get_security_status(db: Any, admin_id: int) -> Optional[AdminSecurityStatus]:
    """Get admin account security posture and metadata."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().get_security_status(admin_id)


def begin_otp_setup(db: Any, admin_id: int, current_password: str) -> AdminLoginResult:
    """Start a new OTP setup flow for the current admin."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().begin_otp_setup(admin_id, current_password)


def disable_otp(
    db: Any, admin_id: int, current_password: str, code: str
) -> Tuple[bool, str]:
    """Disable OTP for the current admin."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().disable_otp(admin_id, current_password, code)


def regenerate_backup_codes(
    db: Any, admin_id: int, current_password: str
) -> Tuple[bool, List[str], str]:
    """Regenerate admin backup codes."""
    if _auth_instance is None or _auth_instance._db is not db:
        init_auth(db)
    return _get_auth().regenerate_backup_codes(admin_id, current_password)
