"""
Admin module - Administrative functions for Plexichat server.
Modularized implementation delegating to sub-modules.
"""

import ipaddress
from typing import Optional, List, Dict, Any, Tuple

from src.core.bounded_lru import BoundedLRU

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


#: Loopback spellings the old implementation special-cased. Hoisted to a
#: module constant so the per-call set literal disappears from the hot path.
_LOCALHOST_VARIANTS = frozenset({"127.0.0.1", "localhost", "::1"})

# ``check_host_restriction`` runs on every admin route. The allowlist is
# config-derived, shared, public data; parsing each CIDR/IP string into an
# ``ipaddress`` object on every request was a fixed CPU cost on that hot path.
# Memoize the *compiled allowlist* (never the verdict: that still depends on
# the per-request client IP and is recomputed every call) in a small bounded
# TTL cache keyed by the allowlist contents. A content key means a config
# change is observed immediately via a cache miss, with no explicit hook; the
# TTL and entry cap only bound memory held for superseded allowlists.
_HOST_RULES_CACHE_MAX_ENTRIES = 64
_HOST_RULES_CACHE_MAX_BYTES = 64 * 1024
_HOST_RULES_CACHE_TTL_SECONDS = 5.0

_host_rules_cache = BoundedLRU(
    max_entries=_HOST_RULES_CACHE_MAX_ENTRIES,
    max_bytes=_HOST_RULES_CACHE_MAX_BYTES,
    ttl_seconds=_HOST_RULES_CACHE_TTL_SECONDS,
)


def invalidate_host_rules_cache() -> None:
    """Drop compiled allowlists (config reload / tests). TTL already bounds them."""
    _host_rules_cache.clear()


def get_host_rules_cache_stats() -> dict:
    """Cache bounds/counters snapshot (observability and regression tests)."""
    stats = dict(_host_rules_cache.stats())
    stats["ttl_seconds"] = _HOST_RULES_CACHE_TTL_SECONDS
    return dict(stats)


def _compile_host_rules(allowed_hosts):
    """Parse an allowlist into ipaddress objects exactly once.

    The three returned values mirror the three decisions the original loop
    made per entry: pre-parsed network/address rules, the raw literal set (for
    malformed client IPs and malformed entries), and whether any entry was a
    loopback spelling (for the loopback peer short-circuit).
    """
    rules = []
    literals = set()
    has_loopback = False
    for allowed in allowed_hosts:
        literals.add(allowed)
        if allowed in _LOCALHOST_VARIANTS:
            has_loopback = True
        if "/" in allowed:
            try:
                rules.append((0, ipaddress.ip_network(allowed, strict=False)))
                continue
            except ValueError:
                pass
        else:
            try:
                rules.append((1, ipaddress.ip_address(allowed)))
                continue
            except ValueError:
                pass
        # Malformed entry: the original fell back to a literal string compare.
        rules.append((2, allowed))
    return tuple(rules), frozenset(literals), has_loopback


def check_host_restriction(client_ip: str, allowed_hosts: List[str]) -> bool:
    """Verify if the client IP is allowed based on host restrictions.

    An empty allowlist fails CLOSED. Disabling the gate entirely is an
    explicit operator choice (``admin_ui.host_restriction.enabled = false``);
    an empty/missing list is a misconfiguration and must never turn the admin
    host restriction into an allow-all rule.
    """
    if not allowed_hosts:
        return False

    # Cache only public, config-derived string allowlists. Any unusual input
    # (e.g. non-string entries) takes the same uncompiled path, preserving the
    # original behaviour for those callers.
    if isinstance(allowed_hosts, (list, tuple)) and all(
        isinstance(entry, str) for entry in allowed_hosts
    ):
        key = tuple(allowed_hosts)
        compiled = _host_rules_cache.get(key)
        if compiled is None:
            compiled = _compile_host_rules(allowed_hosts)
            _host_rules_cache.set(key, compiled)
    else:
        compiled = _compile_host_rules(allowed_hosts)
    rules, literals, has_loopback = compiled

    if client_ip in _LOCALHOST_VARIANTS:
        # Original: any loopback spelling in the allowlist admits a loopback
        # peer. Preserved verbatim (incl. its loose, non-matching semantics).
        return has_loopback

    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip in literals

    for kind, value in rules:
        if kind == 0:  # parsed CIDR
            if client in value:
                return True
        elif kind == 1:  # parsed exact address
            if client == value:
                return True
        else:  # malformed entry -> literal compare
            if client_ip == value:
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


def get_banned_usernames(
    limit: int = 200, offset: int = 0
) -> List[AdminBannedUsername]:
    """Retrieve a bounded page of banned username patterns."""
    return users.get_banned_usernames(_get_db(), limit, offset)


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


def force_password_change(
    acting_admin_id: int,
    target_admin_id: int,
    client_ip: str = "unknown",
) -> Tuple[bool, str]:
    """Force another administrator to change their password on next login.

    Routes pass an explicit ``int`` here; coercion is delegated to the
    underlying service method so bad input produces a clean 400 instead
    of a 500 from a swallowed :class:`ValueError`.  The audit log entry
    and the SQL UPDATE are written back-to-back by the service layer, so
    callers never need to drop into raw SQL or to ``log_admin_action``
    themselves.
    """
    from .auth.password_mixin import force_password_change_target
    from .permissions import can_manage_admin

    db = _get_db()

    # This is a support-tool style mutation against another *admin* account, so
    # it must honour the same role-position hierarchy every other admin-targeting
    # mutation enforces (admin_user.update/delete/toggle_status, ban/unban admin).
    # Historically only self-targeting was refused here, which let any holder of
    # ``admin.edit`` (e.g. a support_admin at position 10) force a password change
    # on a super_admin (position 100) and evict a superior account. Fail closed
    # when the ids cannot be coerced or the hierarchy lookup errors.
    try:
        actor_id = int(acting_admin_id)
        target_id = int(target_admin_id)
        manageable = can_manage_admin(db, actor_id, target_id)
    except (TypeError, ValueError):
        return False, "Invalid admin ID"
    except Exception:
        import utils.logger as logger

        logger.error(
            "force_password_change REFUSED: hierarchy check failed for %s -> %s",
            acting_admin_id,
            target_admin_id,
            exc_info=True,
        )
        return False, "Could not verify admin hierarchy"
    if not manageable:
        return False, "Insufficient role position to force this admin's password change"

    result = force_password_change_target(
        db=db,
        acting_admin_id=actor_id,
        target_admin_id=target_id,
        client_ip=client_ip,
    )
    # The force_password_change flag is only consulted at login
    # (check_password_change_required); no admin route gates on it. Without
    # dropping the target's existing admin sessions, a bearer token minted
    # before this operator-mandated revocation keeps authorising admin actions
    # until natural expiry. Every other password-change path revokes sessions;
    # do the same here, and only on the success path so a refused/failed
    # mutation (e.g. audit failure) has no side effect.
    if result[0]:
        db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (target_id,))
    return result


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
    "invalidate_host_rules_cache",
    "get_host_rules_cache_stats",
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
