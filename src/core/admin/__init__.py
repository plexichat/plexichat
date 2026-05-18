"""
Admin module - Administrative functions for Plexichat server.
Modularized implementation delegating to sub-modules.
"""

from typing import Optional, List, Dict, Any, Tuple

# Import types and classes for public API
from .auth import AdminLoginResult
from .tickets import FeedbackTicket, AdminNote
from .moderation import HashReport, BlockedHash, BlockedUser
from .users import AdminUserDetail, AdminBannedUsername
from .system import get_system_metrics

# Re-export modules
from . import auth, tickets, moderation, users, system, logs

__all__ = ["auth", "tickets", "moderation", "users", "system", "logs"]

_db: Any = None
_auth_mod: Any = None
_features: Any = None
_setup_complete = False


def setup(
    db: Any, auth_module: Optional[Any] = None, features_module: Optional[Any] = None
) -> None:
    """Initialize the admin module."""
    global _db, _auth_mod, _features, _setup_complete
    _db = db
    _auth_mod = auth_module
    _features = features_module
    _setup_complete = True
    auth.ensure_admin_user(_db)


def is_setup() -> bool:
    """Check if admin module is initialized."""
    return _setup_complete


def _get_db():
    if not _setup_complete or _db is None:
        raise RuntimeError("Admin not initialized. Call admin.setup(db) first.")
    return _db


# --- Public API wrappers for backward compatibility ---


def login(username: str, password: str, ip: str = "unknown") -> AdminLoginResult:
    """Authenticate an administrator.

    Uses admin.auth.authenticate_admin() which validates credentials against
    admin_users and creates sessions in admin_sessions — the same table that
    get_admin_from_token() validates against.  This ensures the token returned
    at login can actually be used for subsequent admin API calls.
    """
    return auth.authenticate_admin(_get_db(), username, password, ip)


def verify_otp_setup(
    admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify and finalize OTP setup for an administrator."""
    return auth.verify_otp_setup(_get_db(), admin_id, code, challenge_token)


def verify_otp(admin_id: int, code: str, challenge_token: str) -> AdminLoginResult:
    """Verify an OTP code for an administrator login."""
    return auth.verify_otp(_get_db(), admin_id, code, challenge_token)


def validate_session(token: str) -> Optional[int]:
    """Validate an administrator session token."""
    return auth.validate_session(_get_db(), token)


def logout(token: str) -> bool:
    """Invalidate an administrator session."""
    return auth.logout(_get_db(), token)


def get_feedback_tickets(
    status_filter: Optional[str] = None, limit: int = 50, offset: int = 0
) -> List[FeedbackTicket]:
    """Retrieve a list of feedback tickets."""
    return tickets.get_feedback_tickets(_get_db(), status_filter, limit, offset)


def get_ticket(ticket_id: int) -> Optional[FeedbackTicket]:
    """Retrieve a specific feedback ticket by ID."""
    return tickets.get_ticket(_get_db(), ticket_id)


def update_ticket_status(ticket_id: int, status: str, admin_id: int) -> bool:
    """Update the status of a feedback ticket."""
    return tickets.update_ticket_status(_get_db(), ticket_id, status, admin_id)


def add_internal_note(
    ticket_id: int, admin_id: int, content: str
) -> Optional[AdminNote]:
    """Add an internal note to a feedback ticket."""
    return tickets.add_internal_note(_get_db(), ticket_id, admin_id, content)


def get_ticket_notes(ticket_id: int) -> List[AdminNote]:
    """Retrieve all internal notes for a feedback ticket."""
    return tickets.get_ticket_notes(_get_db(), ticket_id)


def get_ticket_counts() -> Dict[str, int]:
    """Get the count of feedback tickets grouped by status."""
    return tickets.get_ticket_counts(_get_db())


def check_host_restriction(client_ip: str, allowed_hosts: List[str]) -> bool:
    """Verify if the client IP is allowed based on host restrictions."""
    if not allowed_hosts:
        return True
    localhost_variants = ["127.0.0.1", "localhost", "::1"]
    for allowed in allowed_hosts:
        if (
            allowed in localhost_variants and client_ip in localhost_variants
        ) or client_ip == allowed:
            return True
        if "/" in allowed and client_ip.startswith(
            allowed.split("/")[0].rsplit(".", 1)[0]
        ):
            return True
    return False


def get_hash_reports(
    status_filter: Optional[str] = None, limit: int = 50, offset: int = 0
) -> List[HashReport]:
    """Retrieve content hash reports."""
    return moderation.get_hash_reports(_get_db(), status_filter, limit, offset)


def get_hash_report_counts() -> Dict[str, int]:
    """Get count of hash reports grouped by status."""
    return moderation.get_hash_report_counts(_get_db())


def get_blocked_hashes(limit: int = 100, offset: int = 0) -> List[BlockedHash]:
    """Retrieve the list of blocked content hashes."""
    return moderation.get_blocked_hashes(_get_db(), limit, offset)


def get_blocked_hash_count() -> int:
    """Get the total number of blocked content hashes."""
    return moderation.get_blocked_hash_count(_get_db())


def review_hash_report(
    report_id: int, admin_id: int, action: str, notes: Optional[str] = None
) -> bool:
    """Submit a review for a content hash report."""
    return moderation.review_hash_report(_get_db(), report_id, admin_id, action, notes)


def unblock_hash(hash_value: str) -> bool:
    """Remove a hash from the blocklist."""
    return moderation.unblock_hash(_get_db(), hash_value)


def block_hash(
    hash_value: str,
    reason: str,
    admin_id: int,
    hash_type: str = "sha256",
    phash_threshold: int = 10,
) -> bool:
    """Add a hash to the blocklist."""
    return moderation.block_hash(
        _get_db(), hash_value, reason, admin_id, hash_type, phash_threshold
    )


def get_blocked_users(limit: int = 100, offset: int = 0) -> List[BlockedUser]:
    """Retrieve the list of blocked users."""
    return moderation.get_blocked_users(_get_db(), limit, offset)


def block_user(
    user_id: int, reason: str, admin_id: int, duration_hours: Optional[int] = None
) -> bool:
    """Block a user from the platform."""
    return moderation.block_user(_get_db(), user_id, reason, admin_id, duration_hours)


def unblock_user(user_id: int) -> bool:
    """Unblock a user."""
    return moderation.unblock_user(_get_db(), user_id)


def search_users(q: str, limit: int = 20, offset: int = 0) -> List[AdminUserDetail]:
    """Search for users by username or email."""
    return users.search_users(_get_db(), q, limit, offset)


def get_user_details(user_id: int) -> Optional[AdminUserDetail]:
    """Retrieve detailed information for a user."""
    return users.get_user_details(_get_db(), user_id)


def force_username_change(user_id: int, forced: bool = True) -> bool:
    """Require a user to change their username."""
    return users.force_username_change(_get_db(), user_id, forced)


def get_banned_usernames() -> List[AdminBannedUsername]:
    """Retrieve the list of banned username patterns."""
    return users.get_banned_usernames(_get_db())


def add_banned_username(
    pattern: str, reason: str, admin_id: int, is_regex: bool = False
) -> bool:
    """Add a pattern to the banned usernames list."""
    return users.add_banned_username(_get_db(), pattern, reason, admin_id, is_regex)


def remove_banned_username(pattern_id: int) -> bool:
    """Remove a pattern from the banned usernames list."""
    return users.remove_banned_username(_get_db(), pattern_id)


def update_user_tier(user_id: int, tier: str, admin_id: int = 0) -> bool:
    """Update the account tier for a user."""
    return users.update_user_tier(_get_db(), user_id, tier, admin_id, _features)


def update_user_badges(user_id: int, badges: List[str], admin_id: int = 0) -> bool:
    """Update the set of badges assigned to a user."""
    return users.update_user_badges(_get_db(), user_id, badges, admin_id)


def add_user_badge(user_id: int, badge: str, admin_id: int = 0) -> Optional[List[str]]:
    """Assign a badge to a user."""
    return users.add_user_badge(_get_db(), user_id, badge, admin_id, _features)


def remove_user_badge(
    user_id: int, badge: str, admin_id: int = 0
) -> Optional[List[str]]:
    """Remove a badge from a user."""
    return users.remove_user_badge(_get_db(), user_id, badge, admin_id, _features)


def is_admin(user_id: int) -> bool:
    """Check if a user has administrator privileges."""
    return users.is_admin(_get_db(), user_id)


def set_admin(user_id: int, admin_status: bool) -> bool:
    """Grant or revoke administrator privileges for a user."""
    return users.set_admin(_get_db(), user_id, admin_status)


def lock_user(user_id: int, duration_seconds: Optional[int] = None) -> bool:
    """Lock a user account for a specified duration."""
    return users.lock_user(_get_db(), user_id, duration_seconds, _auth_mod)


def unlock_user(user_id: int) -> bool:
    """Unlock a user account."""
    return users.unlock_user(_get_db(), user_id)


def change_password(
    admin_id: int, current_password: str, new_password: str
) -> Tuple[bool, str]:
    """Change the password for an administrator."""
    return auth.change_password(_get_db(), admin_id, current_password, new_password)


def get_security_status(admin_id: int):
    """Get admin account security posture and metadata."""
    return auth.get_security_status(_get_db(), admin_id)


def begin_otp_setup(admin_id: int, current_password: str) -> AdminLoginResult:
    """Start a new OTP setup flow for the current admin."""
    return auth.begin_otp_setup(_get_db(), admin_id, current_password)


def disable_otp(admin_id: int, current_password: str, code: str) -> Tuple[bool, str]:
    """Disable OTP for the current admin."""
    return auth.disable_otp(_get_db(), admin_id, current_password, code)


def regenerate_backup_codes(
    admin_id: int, current_password: str
) -> Tuple[bool, List[str], str]:
    """Regenerate admin backup codes."""
    return auth.regenerate_backup_codes(_get_db(), admin_id, current_password)


def get_user_notes(user_id: int) -> str:
    """Retrieve internal notes for a user."""
    return users.get_user_notes(_get_db(), user_id)


def save_user_notes(user_id: int, notes: str, admin_id: int) -> bool:
    """Save internal notes for a user."""
    return users.save_user_notes(_get_db(), user_id, notes, admin_id)


__all__ = [
    "setup",
    "is_setup",
    "login",
    "verify_otp_setup",
    "verify_otp",
    "validate_session",
    "logout",
    "get_feedback_tickets",
    "get_ticket",
    "update_ticket_status",
    "add_internal_note",
    "get_ticket_notes",
    "get_ticket_counts",
    "check_host_restriction",
    "FeedbackTicket",
    "AdminNote",
    "AdminLoginResult",
    "get_hash_reports",
    "get_hash_report_counts",
    "get_blocked_hashes",
    "get_blocked_hash_count",
    "review_hash_report",
    "unblock_hash",
    "block_hash",
    "HashReport",
    "BlockedHash",
    "get_blocked_users",
    "block_user",
    "unblock_user",
    "BlockedUser",
    "search_users",
    "get_user_details",
    "force_username_change",
    "get_banned_usernames",
    "add_banned_username",
    "remove_banned_username",
    "update_user_tier",
    "update_user_badges",
    "add_user_badge",
    "remove_user_badge",
    "AdminUserDetail",
    "is_admin",
    "set_admin",
    "lock_user",
    "unlock_user",
    "change_password",
    "get_security_status",
    "begin_otp_setup",
    "disable_otp",
    "regenerate_backup_codes",
    "get_user_notes",
    "save_user_notes",
    "get_system_metrics",
]
