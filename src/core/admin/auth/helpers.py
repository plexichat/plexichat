"""
Private helper functions for admin authentication.
"""

import hashlib
import os
import secrets
import string
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger


def _hash_admin_token(token: str) -> str:
    """Return a deterministic one-way fingerprint for an admin bearer token.

    Password hashes must be salted, but session validation needs to look up a
    row from the presented token. The token is generated from high-entropy
    randomness, so a SHA-256 fingerprint is appropriate here and avoids
    storing the bearer value itself. This is deliberately separate from the
    Argon2id password hashing path.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _check_otp_attempt_limit(
    challenge_token: str, max_attempts: int = 5, window_seconds: int = 300
) -> bool:
    """Atomically enforce a bounded OTP challenge attempt budget."""
    from src.core.database import check_rate_limit

    allowed, _remaining = check_rate_limit(
        f"admin_otp:{challenge_token}", max_attempts, window_seconds
    )
    return bool(allowed)


def _consume_otp_challenge(challenge_token: str) -> None:
    from src.core.database import cache_delete

    cache_delete(f"admin_otp_challenge:{challenge_token}")
    # The atomic rate-limit bucket expires naturally; explicitly deleting the
    # challenge is sufficient and avoids a second non-atomic counter path.


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
    """Hand the generated bootstrap admin password to the operator.

    SECURITY: the plaintext password is NEVER written to disk by default.
    A plaintext credential file at rest survives reboots, leaks through
    backups, crash dumps and anything that reads the operator's home
    directory — it is the classic first-boot credential exposure. Instead
    the password is printed ONCE to the operator console (stdout) at the
    moment of generation; it exists nowhere else but the password hash.

    For headless installs that cannot read the console, operators may set
    PLEXICHAT_SAVE_ADMIN_CREDENTIALS=1 to explicitly opt in to the
    (0600, atomic-write) credential file. The write path is hardened:
    mkstemp with 0600 + atomic replace, and any failure is FATAL (raises)
    so first boot can never silently fall back to an unknown password.
    """
    opt_in = os.environ.get("PLEXICHAT_SAVE_ADMIN_CREDENTIALS", "") == "1"

    # Always surface the password exactly once on the console.
    print(
        "\n"
        + "=" * 60
        + "\nPLEXICHAT FIRST BOOT: generated admin credentials"
        + "\n  Username: admin"
        + f"\n  Password: {password}"
        + "\n  Store this password NOW - it is not shown again and"
        + "\n  is not written to disk unless you opted in via"
        + "\n  PLEXICHAT_SAVE_ADMIN_CREDENTIALS=1."
        + "\n"
        + "=" * 60
        + "\n",
        flush=True,
    )

    if not opt_in:
        return

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
    temporary_path = None
    try:
        # Write beside the destination with restrictive creation mode, then
        # atomically replace the destination. This avoids exposing new
        # credentials through an existing permissive file mode and preserves
        # the old file if secure preparation fails.
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{creds_file.name}.", suffix=".tmp", dir=str(home_dir)
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.replace(temporary_path, creds_file)
        temporary_path = None
    except OSError as exc:
        logger.critical(
            "Could not create private admin credential file %s: %s",
            creds_file,
            exc,
        )
        raise RuntimeError("Admin credentials could not be stored securely") from exc
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    logger.warning(f"Admin credentials file written (operator opt-in): {creds_file}")


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
    """Atomically limit admin login attempts by the trusted client address.

    The limiter consumes one slot for each login attempt. Successful login
    resets the bucket in ``AuthenticationMixin``; failed attempts remain
    counted across workers and processes.
    """
    from src.core.database import check_rate_limit

    # Use the longer lockout window for the shared atomic bucket. This avoids
    # the old cache_get/list/cache_set race and keeps the denial effective
    # across workers without a separate non-atomic lockout key.
    allowed, _remaining = check_rate_limit(
        f"admin_login:{ip}", max_attempts, max(window_seconds, lockout_seconds)
    )
    if allowed:
        return True, None
    return False, lockout_seconds
