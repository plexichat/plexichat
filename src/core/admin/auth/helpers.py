"""
Private helper functions for admin authentication.
"""

import hashlib
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger


def _hash_admin_token(token: str) -> str:
    """Hash admin bearer tokens before persistence."""
    return "sha256$" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_otp_challenge(admin_id: int, is_setup: bool, ttl_seconds: int = 300) -> str:
    """Create a short-lived challenge token for OTP verification binding."""
    from src.core.database import cache_set

    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + ttl_seconds
    payload = {
        "admin_id": admin_id,
        "is_setup": is_setup,
        "expires_at": expires_at,
    }
    cache_set(f"admin_otp_challenge:{token}", payload, ttl=ttl_seconds)
    return token


def _validate_otp_challenge(
    challenge_token: str, admin_id: int, is_setup: bool
) -> bool:
    """Validate OTP challenge token against admin and flow type."""
    from src.core.database import cache_get

    now = int(time.time())
    key = f"admin_otp_challenge:{challenge_token}"
    payload = cache_get(key)

    if not payload:
        logger.debug(f"OTP validation failed: No payload for key {key}")
        return False

    if payload["expires_at"] < now:
        logger.debug("OTP validation failed: Challenge expired")
        return False

    if payload["admin_id"] != admin_id or payload["is_setup"] != is_setup:
        logger.debug(
            f"OTP validation failed: Payload mismatch (Expected ID {admin_id}, got {payload['admin_id']}; Setup Expected {is_setup}, got {payload['is_setup']})"
        )
        return False

    return True


def _consume_otp_challenge(challenge_token: str) -> None:
    from src.core.database import cache_delete

    cache_delete(f"admin_otp_challenge:{challenge_token}")


def _generate_password(length: int = 24) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _verify_admin_password(
    db: Any, admin_id: int, current_password: str
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Verify the current admin password and return the account row."""
    row = db.fetch_one(
        """
        SELECT username, email, password_hash, created_at, last_login,
               totp_enabled, must_setup_otp, backup_codes
        FROM admin_users
        WHERE id = ?
        """,
        (admin_id,),
    )
    if not row:
        return False, None, "Admin user not found"

    if not isinstance(row, dict):
        row = {
            "username": row[0],
            "email": row[1],
            "password_hash": row[2],
            "created_at": row[3],
            "last_login": row[4],
            "totp_enabled": row[5],
            "must_setup_otp": row[6],
            "backup_codes": row[7],
        }

    import src.utils.encryption as encryption

    if not encryption.verify_password(current_password, row["password_hash"]):
        return False, None, "Incorrect current password"
    return True, row, ""


def _save_admin_credentials(password: str) -> None:
    """Save admin credentials to a secure file."""
    home_dir = Path.home() / ".plexichat"
    creds_file = home_dir / "admin_credentials.txt"
    home_dir.mkdir(parents=True, exist_ok=True)
    content = f"""Plexichat Admin Credentials
============================
Generated: {time.strftime("%Y-%m-%d %H:%M:%S")}

Username: admin
Password: {password}
Email: admin@example.com (change this in admin settings)

IMPORTANT:
- Change this password after first login
- Set up 2FA (required on first login)
- Delete this file after noting the credentials
- Keep these credentials secure!

Login URL: https://<your-server>:8000/admin
"""
    creds_file.write_text(content)
    try:
        os.chmod(creds_file, 0o600)
    except Exception:
        pass
    logger.warning(f"Admin credentials saved to: {creds_file}")


def _sync_password_to_auth_users(db: Any, username: str, password_hash: str) -> None:
    """Sync the password hash from admin_users to auth_users for the given username.

    Keeps both tables consistent so any code path that checks auth_users
    (e.g. user-facing APIs, future auth unification) sees the correct hash.
    Also ensures cache invalidation so the new hash is picked up immediately.
    """
    try:
        row = db.fetch_one("SELECT id FROM auth_users WHERE username = ?", (username,))
        if row:
            user_id = row["id"] if isinstance(row, dict) else row[0]
            db.execute(
                "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
            try:
                from src.core.database import invalidate_pattern

                invalidate_pattern(f"user_data:{user_id}*")
            except Exception:
                pass
        else:
            logger.warning(
                "No auth_users row for admin '%s' — password not synced", username
            )
    except Exception as exc:
        logger.error(
            "Failed to sync password to auth_users for '%s': %s", username, exc
        )


def _check_rate_limit(
    ip: str,
    max_attempts: int = 5,
    window_seconds: int = 300,
    lockout_seconds: int = 900,
) -> Tuple[bool, Optional[int]]:
    from src.core.database import cache_get, cache_set, cache_delete

    now = time.time() * 1000

    lockout_key = f"admin_login_lockout:{ip}"
    lockout = cache_get(lockout_key)
    if lockout:
        if now < lockout:
            return False, int((lockout - now) / 1000)
        else:
            cache_delete(lockout_key)

    attempts_key = f"admin_login_attempts:{ip}"
    attempts = cache_get(attempts_key) or []

    cutoff = now - (window_seconds * 1000)
    attempts = [t for t in attempts if t > cutoff]

    if len(attempts) >= max_attempts:
        cache_set(lockout_key, now + (lockout_seconds * 1000), ttl=lockout_seconds)
        return False, lockout_seconds

    return True, None
