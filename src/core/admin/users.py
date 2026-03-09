"""
User management for Plexichat Admin.
"""

from typing import List, Optional, Any
from dataclasses import dataclass
import time
import json
import utils.logger as logger
from src.core.database import invalidate_pattern


@dataclass
class AdminUserDetail:
    """Detailed user information for admin view."""

    id: int
    username: str
    email: Optional[str]
    tier: str
    badges: List[str]
    created_at: int
    last_login: Optional[int]
    account_locked: bool = False
    locked_until: Optional[int] = None
    force_username_change: bool = False


@dataclass
class AdminBannedUsername:
    """A pattern for banned usernames."""

    id: int
    pattern: str
    is_regex: bool
    reason: Optional[str]
    created_by: Optional[int]
    created_at: Any


def search_users(
    db: Any, q: str, limit: int = 20, offset: int = 0
) -> List[AdminUserDetail]:
    """Search users by username or ID."""
    try:
        user_id = int(q)
        rows = db.fetch_all(
            """SELECT u.id, u.username, u.email_index, f.rate_limit_tier as tier, f.badges, u.created_at, u.account_locked, u.locked_until, u.force_username_change
               FROM auth_users u
               LEFT JOIN user_features f ON u.id = f.user_id
               WHERE u.id = ? LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        )
    except ValueError:
        rows = db.fetch_all(
            """SELECT u.id, u.username, u.email_index, f.rate_limit_tier as tier, f.badges, u.created_at, u.account_locked, u.locked_until, u.force_username_change
               FROM auth_users u
               LEFT JOIN user_features f ON u.id = f.user_id
               WHERE u.username LIKE ? OR u.email_index LIKE ? LIMIT ? OFFSET ?""",
            (f"%{q}%", f"%{q}%", limit, offset),
        )

    users = []
    for row in rows:
        if isinstance(row, dict):
            badges_json = row.get("badges", "[]") or "[]"
            try:
                badges = (
                    json.loads(badges_json)
                    if isinstance(badges_json, str)
                    else badges_json or []
                )
            except Exception:
                badges = []
            email_display = "[Encrypted]" if row.get("email_index") else None
            users.append(
                AdminUserDetail(
                    id=row["id"],
                    username=row["username"],
                    email=email_display,
                    tier=row.get("tier") or "standard",
                    badges=badges,
                    created_at=row["created_at"],
                    last_login=None,  # Not in search results for performance
                    account_locked=bool(row.get("account_locked", 0)),
                    locked_until=row.get("locked_until"),
                    force_username_change=bool(row.get("force_username_change", 0)),
                )
            )
        else:
            badges_json = row[4] or "[]"
            try:
                badges = (
                    json.loads(badges_json)
                    if isinstance(badges_json, str)
                    else badges_json or []
                )
            except Exception:
                badges = []
            email_display = "[Encrypted]" if row[2] else None
            users.append(
                AdminUserDetail(
                    id=row[0],
                    username=row[1],
                    email=email_display,
                    tier=row[3] or "standard",
                    badges=badges,
                    created_at=row[5],
                    last_login=None,
                    account_locked=bool(row[6]),
                    locked_until=row[7],
                    force_username_change=bool(row[8]),
                )
            )
    return users


def get_user_details(db: Any, user_id: int) -> Optional[AdminUserDetail]:
    """Get full user details by ID."""
    row = db.fetch_one(
        """SELECT u.id, u.username, u.email_index, f.rate_limit_tier as tier, f.badges, u.created_at, u.last_login_at, u.account_locked, u.locked_until, u.force_username_change
           FROM auth_users u
           LEFT JOIN user_features f ON u.id = f.user_id
           WHERE u.id = ?""",
        (user_id,),
    )
    if not row:
        return None

    if isinstance(row, dict):
        badges_json = row.get("badges", "[]") or "[]"
        try:
            badges = (
                json.loads(badges_json)
                if isinstance(badges_json, str)
                else badges_json or []
            )
        except Exception:
            badges = []
        email_display = "[Encrypted]" if row.get("email_index") else None
        return AdminUserDetail(
            id=row["id"],
            username=row["username"],
            email=email_display,
            tier=row.get("tier") or "standard",
            badges=badges,
            created_at=row["created_at"],
            last_login=row.get("last_login_at"),
            account_locked=bool(row.get("account_locked", 0)),
            locked_until=row.get("locked_until"),
            force_username_change=bool(row.get("force_username_change", 0)),
        )
    else:
        badges_json = row[4] or "[]"
        try:
            badges = (
                json.loads(badges_json)
                if isinstance(badges_json, str)
                else badges_json or []
            )
        except Exception:
            badges = []
        email_display = "[Encrypted]" if row[2] else None
        return AdminUserDetail(
            id=row[0],
            username=row[1],
            email=email_display,
            tier=row[3] or "standard",
            badges=badges,
            created_at=row[5],
            last_login=row[6],
            account_locked=bool(row[7]),
            locked_until=row[8],
            force_username_change=bool(row[9]),
        )


def force_username_change(db: Any, user_id: int, forced: bool = True) -> bool:
    """Force a user to change their username on next login."""
    db.execute(
        "UPDATE auth_users SET force_username_change = ? WHERE id = ?",
        (forced, user_id),
    )
    invalidate_pattern("token_verify:*")
    return True


def get_banned_usernames(db: Any) -> List[AdminBannedUsername]:
    """Get list of banned username patterns."""
    rows = db.fetch_all("SELECT * FROM username_blacklist ORDER BY created_at DESC")
    result = []
    for row in rows:
        if isinstance(row, dict):
            row["is_regex"] = bool(row["is_regex"])
            result.append(AdminBannedUsername(**row))
        else:
            result.append(
                AdminBannedUsername(
                    id=row[0],
                    pattern=row[1],
                    is_regex=bool(row[2]),
                    reason=row[3],
                    created_by=row[4],
                    created_at=row[5],
                )
            )
    return result


def add_banned_username(
    db: Any, pattern: str, reason: str, admin_id: int, is_regex: bool = False
) -> bool:
    """Add a pattern to the username blacklist."""
    try:
        db.execute(
            "INSERT INTO username_blacklist (pattern, is_regex, reason, created_by) VALUES (?, ?, ?, ?)",
            (pattern, is_regex, reason, admin_id),
        )
        return True
    except Exception as e:
        logger.error(f"Failed to ban username pattern: {e}")
        return False


def remove_banned_username(db: Any, pattern_id: int) -> bool:
    """Remove a pattern from the username blacklist."""
    db.execute("DELETE FROM username_blacklist WHERE id = ?", (pattern_id,))
    return True


def update_user_tier(
    db: Any, user_id: int, tier: str, admin_id: int = 0, features_module: Any = None
) -> bool:
    """Update a user's tier."""
    if features_module:
        try:
            features_module.set_user_tier(user_id, admin_id, tier)
            invalidate_pattern(f"user_data:{user_id}")
            invalidate_pattern(f"current_user_api:{user_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to update tier via features module: {e}")

    row = db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user_id,))
    if not row:
        return False

    if db.table_exists("user_features"):
        db.execute(
            """INSERT INTO user_features (user_id, rate_limit_tier, granted_by, granted_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET rate_limit_tier = ?, granted_by = ?, granted_at = ?""",
            (
                user_id,
                tier,
                admin_id,
                int(time.time() * 1000),
                tier,
                admin_id,
                int(time.time() * 1000),
            ),
        )
    else:
        db.execute("UPDATE auth_users SET tier = ? WHERE id = ?", (tier, user_id))

    invalidate_pattern(f"user_data:{user_id}")
    invalidate_pattern(f"current_user_api:{user_id}")
    return True


def update_user_badges(
    db: Any, user_id: int, badges: List[str], admin_id: int = 0
) -> bool:
    """Update a user's badges."""
    row = db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user_id,))
    if not row:
        return False
    now = int(time.time() * 1000)
    badges_json = json.dumps(badges)
    if db.table_exists("user_features"):
        db.execute(
            """INSERT INTO user_features (user_id, badges, granted_by, granted_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET badges = ?, granted_by = ?, granted_at = ?""",
            (user_id, badges_json, admin_id, now, badges_json, admin_id, now),
        )
    else:
        db.execute(
            "UPDATE auth_users SET badges = ? WHERE id = ?", (badges_json, user_id)
        )
    return True


def add_user_badge(
    db: Any, user_id: int, badge: str, admin_id: int = 0, features_module: Any = None
) -> Optional[List[str]]:
    """Add a badge to a user if they don't have it."""
    if features_module:
        try:
            features_module.add_badge(user_id, admin_id, badge)
            invalidate_pattern(f"user_data:{user_id}")
            invalidate_pattern(f"current_user_api:{user_id}")
            f = features_module.get_user_features(user_id)
            return f.badges if f else []
        except Exception as e:
            logger.warning(f"Failed to add badge via features module: {e}")

    row = db.fetch_one("SELECT badges FROM user_features WHERE user_id = ?", (user_id,))
    if not row:
        update_user_badges(db, user_id, [badge], admin_id)
        invalidate_pattern(f"user_data:{user_id}")
        invalidate_pattern(f"current_user_api:{user_id}")
        return [badge]

    current_badges_json = (row["badges"] if isinstance(row, dict) else row[0]) or "[]"
    try:
        badges_list = json.loads(current_badges_json)
    except Exception:
        badges_list = []

    if badge not in badges_list:
        badges_list.append(badge)
        update_user_badges(db, user_id, badges_list, admin_id)
        invalidate_pattern(f"user_data:{user_id}")
        invalidate_pattern(f"current_user_api:{user_id}")
    return badges_list


def remove_user_badge(
    db: Any, user_id: int, badge: str, admin_id: int = 0, features_module: Any = None
) -> Optional[List[str]]:
    """Remove a badge from a user."""
    if features_module:
        try:
            features_module.remove_badge(user_id, admin_id, badge)
            invalidate_pattern(f"user_data:{user_id}")
            invalidate_pattern(f"current_user_api:{user_id}")
            f = features_module.get_user_features(user_id)
            return f.badges if f else []
        except Exception as e:
            logger.warning(f"Failed to remove badge via features module: {e}")

    row = db.fetch_one("SELECT badges FROM user_features WHERE user_id = ?", (user_id,))
    if not row:
        return None
    current_badges_json = (row["badges"] if isinstance(row, dict) else row[0]) or "[]"
    try:
        badges_list = json.loads(current_badges_json)
    except Exception:
        badges_list = []
    if badge in badges_list:
        badges_list.remove(badge)
        update_user_badges(db, user_id, badges_list, admin_id)
        invalidate_pattern(f"user_data:{user_id}")
        invalidate_pattern(f"current_user_api:{user_id}")
    return badges_list


def is_admin(db: Any, user_id: int) -> bool:
    """Check if a user has admin privileges."""
    row = db.fetch_one("SELECT permissions FROM auth_users WHERE id = ?", (user_id,))
    if not row:
        return False
    perms_json = row["permissions"] if isinstance(row, dict) else row[0]
    try:
        from src.core.auth.permissions import permissions_from_json, has_permission

        perms = permissions_from_json(perms_json)
        return has_permission(perms, "*") or has_permission(perms, "admin.*")
    except Exception:
        return False


def set_admin(db: Any, user_id: int, admin_status: bool) -> bool:
    """Set or unset admin privileges for a user."""
    row = db.fetch_one("SELECT permissions FROM auth_users WHERE id = ?", (user_id,))
    if not row:
        return False
    perms_json = row["permissions"] if isinstance(row, dict) else row[0]
    try:
        from src.core.auth.permissions import permissions_from_json, permissions_to_json

        perms = permissions_from_json(perms_json)
        if admin_status:
            perms["*"] = True
        else:
            perms.pop("*", None)
            for key in list(perms.keys()):
                if key.startswith("admin."):
                    perms.pop(key)
        db.execute(
            "UPDATE auth_users SET permissions = ? WHERE id = ?",
            (permissions_to_json(perms), user_id),
        )
        return True
    except Exception as e:
        logger.error(f"Failed to set admin status: {e}")
        return False


def lock_user(
    db: Any,
    user_id: int,
    duration_seconds: Optional[int] = None,
    auth_module: Any = None,
) -> bool:
    """Lock/suspend a user account."""
    locked_until = (
        int(time.time() + duration_seconds) if duration_seconds is not None else None
    )
    db.execute(
        "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
        (locked_until, user_id),
    )
    if auth_module:
        auth_module.logout_all(user_id)
    invalidate_pattern("token_verify:*")
    return True


def unlock_user(db: Any, user_id: int) -> bool:
    """Unlock/unsuspend a user account."""
    db.execute(
        "UPDATE auth_users SET account_locked = 0, locked_until = NULL WHERE id = ?",
        (user_id,),
    )
    invalidate_pattern("token_verify:*")
    return True


def get_user_notes(db: Any, user_id: int) -> str:
    """Get internal admin notes for a user."""
    row = db.fetch_one("SELECT internal_notes FROM auth_users WHERE id = ?", (user_id,))
    if row:
        return (row["internal_notes"] if isinstance(row, dict) else row[0]) or ""
    return ""


def save_user_notes(db: Any, user_id: int, notes: str, admin_id: int) -> bool:
    """Save internal admin notes for a user."""
    db.execute(
        "UPDATE auth_users SET internal_notes = ? WHERE id = ?", (notes, user_id)
    )
    logger.info(f"Admin {admin_id} updated notes for user {user_id}")
    return True
