"""
Admin authentication and session management for Plexichat Admin.
"""

from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
import time
import secrets
import string
import hashlib
import json
import os
from pathlib import Path
import utils.logger as logger
import utils.config as config


@dataclass
class AdminLoginResult:
    """Result of admin login attempt."""

    success: bool
    token: Optional[str] = None
    user_id: Optional[int] = None
    requires_otp_setup: bool = False
    otp_secret: Optional[str] = None
    otp_qr_uri: Optional[str] = None
    requires_otp_verify: bool = False
    challenge_token: Optional[str] = None
    error: Optional[str] = None
    rate_limited: bool = False
    requires_password_change: bool = False


@dataclass
class AdminSecurityStatus:
    """Current admin account security posture."""

    admin_id: int
    username: str
    email: Optional[str]
    created_at: int
    last_login: Optional[int]
    otp_required: bool
    otp_enabled: bool
    must_setup_otp: bool
    backup_codes_remaining: int


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
    import utils.logger as logger

    now = int(time.time())
    key = f"admin_otp_challenge:{challenge_token}"
    payload = cache_get(key)
    
    if not payload:
        logger.debug(f"OTP validation failed: No payload for key {key}")
        return False
        
    if payload["expires_at"] < now:
        logger.debug(f"OTP validation failed: Challenge expired")
        return False
        
    if payload["admin_id"] != admin_id or payload["is_setup"] != is_setup:
        logger.debug(f"OTP validation failed: Payload mismatch (Expected ID {admin_id}, got {payload['admin_id']}; Setup Expected {is_setup}, got {payload['is_setup']})")
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
            # Invalidate cached user data so the new hash is picked up immediately
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
        # Don't let auth_users sync failures break the admin operation
        logger.error(
            "Failed to sync password to auth_users for '%s': %s", username, exc
        )


def ensure_admin_user(db: Any) -> None:
    """Ensure admin user exists, create with random password if not."""
    row = db.fetch_one("SELECT id FROM admin_users WHERE username = ?", ("admin",))
    if row:
        return
    password = _generate_password()
    import src.utils.encryption as encryption

    password_hash = encryption.hash_password(password)
    admin_id = encryption.generate_snowflake_id()
    now = int(time.time())
    db.execute(
        """INSERT INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, "admin", password_hash, "admin@example.com", now, 1),
    )
    # Sync the password hash to auth_users so core_auth.login() can authenticate
    _sync_password_to_auth_users(db, "admin", password_hash)
    _save_admin_credentials(password)
    logger.info("Created admin user with random password using Argon2id")


def _check_rate_limit(
    ip: str,
    max_attempts: int = 5,
    window_seconds: int = 300,
    lockout_seconds: int = 900,
) -> Tuple[bool, Optional[int]]:
    from src.core.database import cache_get, cache_set, cache_delete

    now = time.time() * 1000

    # Check if IP is locked out
    lockout_key = f"admin_login_lockout:{ip}"
    lockout = cache_get(lockout_key)
    if lockout:
        if now < lockout:
            return False, int((lockout - now) / 1000)
        else:
            # Lockout expired, clear it
            cache_delete(lockout_key)

    # Get existing login attempts
    attempts_key = f"admin_login_attempts:{ip}"
    attempts = cache_get(attempts_key) or []

    # Filter attempts within the window
    cutoff = now - (window_seconds * 1000)
    attempts = [t for t in attempts if t > cutoff]

    # Check if limit exceeded
    if len(attempts) >= max_attempts:
        # Set lockout
        cache_set(lockout_key, now + (lockout_seconds * 1000), ttl=lockout_seconds)
        return False, lockout_seconds

    return True, None


def authenticate_admin(
    db: Any, username: str, password: str, ip: str = "unknown"
) -> AdminLoginResult:
    """Authenticate admin user."""
    from src.core.database import cache_get, cache_set, cache_delete

    admin_config = config.get("admin_ui", {})
    rate_config = admin_config.get("rate_limit", {})
    allowed, wait_seconds = _check_rate_limit(
        ip,
        rate_config.get("max_attempts", 5),
        rate_config.get("window_seconds", 300),
        rate_config.get("lockout_seconds", 900),
    )
    if not allowed:
        return AdminLoginResult(
            success=False,
            error=f"Too many login attempts. Try again in {wait_seconds} seconds.",
            rate_limited=True,
        )

    row = db.fetch_one(
        "SELECT id, password_hash, totp_secret, totp_secret_encrypted, totp_enabled, must_setup_otp FROM admin_users WHERE username = ?",
        (username,),
    )
    if not row:
        attempts_key = f"admin_login_attempts:{ip}"
        attempts = cache_get(attempts_key) or []
        attempts.append(time.time() * 1000)
        cache_set(attempts_key, attempts, ttl=rate_config.get("window_seconds", 300))
        return AdminLoginResult(success=False, error="Invalid credentials")

    if isinstance(row, dict):
        admin_id = row["id"]
        password_hash = row["password_hash"]
        totp_enabled = bool(row["totp_enabled"])
        must_setup_otp = bool(row["must_setup_otp"])
    else:
        # SELECT columns: id(0), password_hash(1), totp_secret(2), totp_secret_encrypted(3), totp_enabled(4), must_setup_otp(5)
        admin_id = row[0]
        password_hash = row[1]
        # Prefer encrypted column (index 3) over plaintext (index 2)
        _totp_secret = (
            row[3] if len(row) > 3 and row[3] else (row[2] if len(row) > 2 else None)
        )
        totp_enabled = bool(row[4]) if len(row) > 4 else False
        must_setup_otp = bool(row[5]) if len(row) > 5 else False

    import src.utils.encryption as encryption

    authenticated = False
    if not password_hash.startswith("$argon2"):
        import hashlib

        if hashlib.sha256(password.encode()).hexdigest() == password_hash:
            authenticated = True
            new_hash = encryption.hash_password(password)
            db.execute(
                "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                (new_hash, admin_id),
            )
            # Sync the upgraded hash to auth_users so core_auth.login() sees it
            _sync_password_to_auth_users(db, username, new_hash)
    else:
        if encryption.verify_password(password, password_hash):
            authenticated = True

    if not authenticated:
        attempts_key = f"admin_login_attempts:{ip}"
        attempts = cache_get(attempts_key) or []
        attempts.append(time.time() * 1000)
        cache_set(attempts_key, attempts, ttl=rate_config.get("window_seconds", 300))
        return AdminLoginResult(success=False, error="Invalid credentials")

    # Clear login attempts on successful authentication
    cache_delete(f"admin_login_attempts:{ip}")
    cache_delete(f"admin_login_lockout:{ip}")

    otp_required = admin_config.get("require_otp", True)
    if not otp_required:
        if must_setup_otp:
            db.execute(
                "UPDATE admin_users SET must_setup_otp = 0 WHERE id = ?", (admin_id,)
            )
        token = create_session(db, admin_id)
        db.execute(
            "UPDATE admin_users SET last_login = ? WHERE id = ?",
            (int(time.time() * 1000), admin_id),
        )

        # Check if password change is required
        requires_password_change = check_password_change_required(db, admin_id)

        return AdminLoginResult(
            success=True,
            token=token,
            user_id=admin_id,
            requires_password_change=requires_password_change,
        )

    # If OTP is required but not yet enabled (regardless of whether a
    # secret already exists), the admin must go through OTP setup.
    if must_setup_otp or not totp_enabled:
        import pyotp
        from src.utils.encryption import encrypt_data as _encrypt

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        qr_uri = totp.provisioning_uri(name=username, issuer_name="Plexichat Admin")
        # Store encrypted TOTP secret instead of plaintext
        encrypted_secret = _encrypt(secret, context=f"admin_totp:{admin_id}")
        db.execute(
            "UPDATE admin_users SET totp_secret_encrypted = ?, totp_secret = NULL, must_setup_otp = 1 WHERE id = ?",
            (encrypted_secret, admin_id),
        )
        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_setup=True,
            otp_secret=secret,
            otp_qr_uri=qr_uri,
            challenge_token=_create_otp_challenge(admin_id, is_setup=True),
        )

    if totp_enabled:
        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_verify=True,
            challenge_token=_create_otp_challenge(admin_id, is_setup=False),
        )

    # Should be unreachable: if OTP is required and totp_enabled is True
    # we returned the verify branch above; if False, the setup branch.
    # Handle gracefully just in case.
    return AdminLoginResult(
        success=True,
        user_id=admin_id,
        requires_otp_verify=True,
        challenge_token=_create_otp_challenge(admin_id, is_setup=False),
    )


def verify_otp_setup(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify OTP code during setup."""
    if not _validate_otp_challenge(challenge_token, admin_id, is_setup=True):
        return AdminLoginResult(success=False, error="Invalid or expired OTP challenge")

    row = db.fetch_one(
        "SELECT totp_secret, totp_secret_encrypted FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if not row:
        return AdminLoginResult(success=False, error="Admin user not found")
    # Prefer encrypted secret, fall back to plaintext for legacy
    encrypted_secret = (
        row.get("totp_secret_encrypted")
        if isinstance(row, dict)
        else (row[1] if len(row) > 1 else None)
    )
    plaintext_secret = row.get("totp_secret") if isinstance(row, dict) else row[0]
    secret = None
    if encrypted_secret:
        try:
            from src.utils.encryption import decrypt_data as _decrypt

            secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
        except Exception:
            secret = plaintext_secret  # Fallback to legacy
    else:
        secret = plaintext_secret
    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")
    import pyotp
    from src.utils.encryption import hash_password as _hash_pwd

    # Replay prevention: check if code was already used
    last_row = db.fetch_one(
        "SELECT otp_last_used_code, otp_last_used_at FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if last_row:
        last_code = (
            last_row.get("otp_last_used_code")
            if isinstance(last_row, dict)
            else last_row[0]
        )
        if last_code == code:
            return AdminLoginResult(
                success=False, error="Code already used — wait for next code"
            )

    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return AdminLoginResult(success=False, error="Invalid OTP code")

    # Record used code for replay prevention
    db.execute(
        "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ? WHERE id = ?",
        (code, int(time.time() * 1000), admin_id),
    )
    db.execute(
        "UPDATE admin_users SET totp_enabled = 1, must_setup_otp = 0 WHERE id = ?",
        (admin_id,),
    )
    # Generate and hash backup codes (never store plaintext)
    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    hashed_codes = [_hash_pwd(c.replace("-", "").lower()) for c in backup_codes]
    db.execute(
        "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ? WHERE id = ?",
        (json.dumps(hashed_codes), admin_id),
    )
    _consume_otp_challenge(challenge_token)

    # Check if password change is required
    requires_password_change = check_password_change_required(db, admin_id)

    return AdminLoginResult(
        success=True,
        token=create_session(db, admin_id),
        user_id=admin_id,
        requires_password_change=requires_password_change,
    )


def verify_otp(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify OTP code for login."""
    if not _validate_otp_challenge(challenge_token, admin_id, is_setup=False):
        return AdminLoginResult(success=False, error="Invalid or expired OTP challenge")

    row = db.fetch_one(
        "SELECT totp_secret, totp_secret_encrypted, backup_codes, backup_codes_hash FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if not row:
        return AdminLoginResult(success=False, error="Admin user not found")
    if isinstance(row, dict):
        encrypted_secret = row.get("totp_secret_encrypted")
        plaintext_secret = row.get("totp_secret")
        backup_codes_plaintext = row.get("backup_codes")
        backup_codes_hashed = row.get("backup_codes_hash")
    else:
        encrypted_secret = row[1] if len(row) > 1 else None
        plaintext_secret = row[0]
        backup_codes_plaintext = row[2] if len(row) > 2 else None
        backup_codes_hashed = row[3] if len(row) > 3 else None

    # Decrypt TOTP secret (prefer encrypted, fallback to plaintext legacy)
    secret = None
    if encrypted_secret:
        try:
            from src.utils.encryption import decrypt_data as _decrypt

            secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
        except Exception:
            secret = plaintext_secret
    else:
        secret = plaintext_secret
    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")

    # Replay prevention
    last_row = db.fetch_one(
        "SELECT otp_last_used_code, otp_last_used_at FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if last_row:
        last_code = (
            last_row.get("otp_last_used_code")
            if isinstance(last_row, dict)
            else last_row[0]
        )
        if last_code == code:
            return AdminLoginResult(
                success=False, error="Code already used — wait for next code"
            )

    import pyotp
    from src.utils.encryption import (
        hash_password as _hash_pwd,
        verify_password as _verify_pwd,
    )

    if pyotp.TOTP(secret).verify(code, valid_window=1):
        # Record used code for replay prevention
        db.execute(
            "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ?, last_login = ? WHERE id = ?",
            (code, int(time.time() * 1000), int(time.time() * 1000), admin_id),
        )
        _consume_otp_challenge(challenge_token)

        # Check if password change is required
        requires_password_change = check_password_change_required(db, admin_id)

        return AdminLoginResult(
            success=True,
            token=create_session(db, admin_id),
            user_id=admin_id,
            requires_password_change=requires_password_change,
        )

    # Try backup codes — prefer hashed codes, fall back to plaintext for legacy
    normalized = code.upper().replace("-", "")
    if backup_codes_hashed:
        try:
            hashed_list = json.loads(backup_codes_hashed)
        except (json.JSONDecodeError, TypeError):
            hashed_list = []
        for i, hashed in enumerate(hashed_list):
            if _verify_pwd(normalized.lower(), str(hashed)):
                # Remove used backup code
                hashed_list.pop(i)
                db.execute(
                    "UPDATE admin_users SET backup_codes_hash = ?, last_login = ? WHERE id = ?",
                    (json.dumps(hashed_list), int(time.time() * 1000), admin_id),
                )
                _consume_otp_challenge(challenge_token)

                # Check if password change is required
                requires_password_change = check_password_change_required(db, admin_id)

                return AdminLoginResult(
                    success=True,
                    token=create_session(db, admin_id),
                    user_id=admin_id,
                    requires_password_change=requires_password_change,
                )
    elif backup_codes_plaintext:
        # Legacy plaintext backup codes (being phased out)
        codes = backup_codes_plaintext.split(",")
        if normalized in codes:
            codes.remove(normalized)
            # Also hash remaining codes during this opportunity
            hashed_remaining = [_hash_pwd(c.lower()) for c in codes if c.strip()]
            db.execute(
                "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ?, last_login = ? WHERE id = ?",
                (json.dumps(hashed_remaining), int(time.time() * 1000), admin_id),
            )
            _consume_otp_challenge(challenge_token)

            # Check if password change is required
            requires_password_change = check_password_change_required(db, admin_id)

            return AdminLoginResult(
                success=True,
                token=create_session(db, admin_id),
                user_id=admin_id,
                requires_password_change=requires_password_change,
            )
    return AdminLoginResult(success=False, error="Invalid OTP code")


def check_password_change_required(db: Any, admin_id: int) -> bool:
    """Check if admin is required to change password."""
    admin_config = config.get("admin_ui", {})

    # Check if force password change is enabled in config
    if not admin_config.get("force_password_change_first_login", True):
        return False

    # Check if admin has force_password_change flag set
    row = db.fetch_one(
        "SELECT force_password_change, last_password_change FROM admin_users WHERE id = ?",
        (admin_id,),
    )

    if not row:
        return False

    if isinstance(row, dict):
        force_change = bool(row.get("force_password_change", 0))
        last_password_change = row.get("last_password_change")
    else:
        force_change = bool(row[0]) if len(row) > 0 else False
        last_password_change = row[1] if len(row) > 1 else None

    # If force_password_change flag is set, require change
    if force_change:
        return True

    # Check if password hasn't been changed in configured interval
    password_policy = admin_config.get("security", {}).get("password_policy", {})
    change_interval_days = password_policy.get("change_interval_days", 90)

    if last_password_change and change_interval_days > 0:
        now = int(time.time())
        days_since_change = (now - last_password_change) / (24 * 3600 * 1000)
        if days_since_change >= change_interval_days:
            return True

    return False


def change_admin_password(
    db: Any, admin_id: int, old_password: str, new_password: str
) -> Tuple[bool, str]:
    """Change admin password with validation."""
    import src.utils.encryption as encryption

    # Get current password hash
    row = db.fetch_one(
        "SELECT password_hash FROM admin_users WHERE id = ?", (admin_id,)
    )

    if not row:
        return False, "Admin user not found"

    if isinstance(row, dict):
        current_hash = row.get("password_hash")
    else:
        current_hash = row[0]

    # Verify old password
    if not current_hash or not encryption.verify_password(
        old_password, str(current_hash)
    ):
        return False, "Current password is incorrect"

    # Validate new password against policy
    admin_config = config.get("admin_ui", {})
    password_policy = admin_config.get("security", {}).get("password_policy", {})

    min_length = password_policy.get("min_length", 12)
    require_uppercase = password_policy.get("require_uppercase", True)
    require_lowercase = password_policy.get("require_lowercase", True)
    require_numbers = password_policy.get("require_numbers", True)
    require_special_chars = password_policy.get("require_special_chars", True)
    prevent_common_passwords = password_policy.get("prevent_common_passwords", True)

    if len(new_password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    if require_uppercase and not any(c.isupper() for c in new_password):
        return False, "Password must contain at least one uppercase letter"

    if require_lowercase and not any(c.islower() for c in new_password):
        return False, "Password must contain at least one lowercase letter"

    if require_numbers and not any(c.isdigit() for c in new_password):
        return False, "Password must contain at least one number"

    if require_special_chars and not any(
        c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in new_password
    ):
        return False, "Password must contain at least one special character"

    if prevent_common_passwords:
        common_passwords = [
            "password",
            "123456",
            "qwerty",
            "admin",
            "letmein",
            "welcome",
            "monkey",
            "dragon",
            "master",
            "hello",
            "football",
            "iloveyou",
        ]
        if new_password.lower() in common_passwords:
            return False, "Password is too common, please choose a stronger password"

    # Hash new password
    new_hash = encryption.hash_password(new_password)

    # Update password and clear force_password_change flag
    now = int(time.time())
    db.execute(
        "UPDATE admin_users SET password_hash = ?, force_password_change = 0, last_password_change = ? WHERE id = ?",
        (new_hash, now, admin_id),
    )

    # Sync to auth_users table
    admin_row = db.fetch_one(
        "SELECT username FROM admin_users WHERE id = ?", (admin_id,)
    )
    if admin_row:
        username = (
            admin_row.get("username") if isinstance(admin_row, dict) else admin_row[0]
        )
        if username:
            _sync_password_to_auth_users(db, str(username), new_hash)

    return True, "Password changed successfully"


def create_session(db: Any, admin_id: int, expires_hours: int = 8) -> str:
    """Create admin session."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_admin_token(token)
    now = int(time.time())
    expires = now + (expires_hours * 3600 * 1000)
    from src.utils.encryption import generate_snowflake_id

    sid = generate_snowflake_id()
    db.execute(
        "INSERT INTO admin_sessions (id, admin_id, token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (sid, admin_id, token_hash, now, expires),
    )
    return token


def validate_session(db: Any, token: str) -> Optional[int]:
    """Validate admin session token."""
    now = int(time.time())
    token_hash = _hash_admin_token(token)
    row = db.fetch_one(
        "SELECT id, admin_id, token FROM admin_sessions WHERE token = ? AND expires_at > ?",
        (token_hash, now),
    )
    if not row:
        # SECURITY: Fallback for legacy plaintext tokens — validate hash then upgrade
        legacy_row = db.fetch_one(
            "SELECT id, admin_id, token FROM admin_sessions WHERE token = ? AND expires_at > ?",
            (token, now),
        )
        if legacy_row:
            if isinstance(legacy_row, dict):
                session_id = legacy_row["id"]
                admin_id = legacy_row["admin_id"]
            else:
                session_id = legacy_row[0]
                admin_id = legacy_row[1]
            # Upgrade plaintext token to hashed token in-place
            try:
                db.execute(
                    "UPDATE admin_sessions SET token = ? WHERE id = ?",
                    (token_hash, session_id),
                )
            except Exception:
                pass
            return admin_id
        return None

    if isinstance(row, dict):
        admin_id = row["admin_id"]
    else:
        admin_id = row[1]
    return admin_id


def logout(db: Any, token: str) -> bool:
    """Invalidate admin session."""
    token_hash = _hash_admin_token(token)
    db.execute(
        "DELETE FROM admin_sessions WHERE token = ? OR token = ?", (token_hash, token)
    )
    return True


def change_password(
    db: Any, admin_id: int, current_password: str, new_password: str
) -> Tuple[bool, str]:
    """Change admin password with policy validation."""
    valid, _row, message = _verify_admin_password(db, admin_id, current_password)
    if not valid:
        return False, message

    # Validate new password against policy
    admin_config = config.get("admin_ui", {})
    password_policy = admin_config.get("security", {}).get("password_policy", {})

    min_length = password_policy.get("min_length", 12)
    require_uppercase = password_policy.get("require_uppercase", True)
    require_lowercase = password_policy.get("require_lowercase", True)
    require_numbers = password_policy.get("require_numbers", True)
    require_special_chars = password_policy.get("require_special_chars", True)
    prevent_common_passwords = password_policy.get("prevent_common_passwords", True)

    if len(new_password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    if require_uppercase and not any(c.isupper() for c in new_password):
        return False, "Password must contain at least one uppercase letter"

    if require_lowercase and not any(c.islower() for c in new_password):
        return False, "Password must contain at least one lowercase letter"

    if require_numbers and not any(c.isdigit() for c in new_password):
        return False, "Password must contain at least one number"

    if require_special_chars and not any(
        c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in new_password
    ):
        return False, "Password must contain at least one special character"

    if prevent_common_passwords:
        common_passwords = [
            "password",
            "123456",
            "qwerty",
            "admin",
            "letmein",
            "welcome",
            "monkey",
            "dragon",
            "master",
            "hello",
            "football",
            "iloveyou",
        ]
        if new_password.lower() in common_passwords:
            return False, "Password is too common, please choose a stronger password"

    import src.utils.encryption as encryption

    new_hash = encryption.hash_password(new_password)
    now = int(time.time())

    # Update password with timestamp and clear force_password_change flag
    db.execute(
        "UPDATE admin_users SET password_hash = ?, force_password_change = 0, last_password_change = ? WHERE id = ?",
        (new_hash, now, admin_id),
    )

    # Sync the new password hash to auth_users so core_auth.login() sees it.
    # Derive username from the already-verified row instead of hardcoding "admin".
    username = _row["username"] if _row and isinstance(_row, dict) else "admin"
    if username:
        _sync_password_to_auth_users(db, str(username), new_hash)
    return True, "Password updated successfully"


def get_security_status(db: Any, admin_id: int) -> Optional[AdminSecurityStatus]:
    """Return security settings and posture for an admin account."""
    row = db.fetch_one(
        """
        SELECT username, email, created_at, last_login, totp_enabled, must_setup_otp, backup_codes
        FROM admin_users
        WHERE id = ?
        """,
        (admin_id,),
    )
    if not row:
        return None
    if isinstance(row, dict):
        username = row["username"]
        email = row["email"]
        created_at = row["created_at"]
        last_login = row["last_login"]
        totp_enabled = bool(row["totp_enabled"])
        must_setup_otp = bool(row["must_setup_otp"])
        backup_codes = row["backup_codes"] or ""
    else:
        (
            username,
            email,
            created_at,
            last_login,
            totp_enabled,
            must_setup_otp,
            backup_codes,
        ) = row

    # Count hashed backup codes (new secure format)
    hash_row = db.fetch_one(
        "SELECT backup_codes_hash FROM admin_users WHERE id = ?", (admin_id,)
    )
    if hash_row:
        hashed = (
            hash_row.get("backup_codes_hash")
            if isinstance(hash_row, dict)
            else hash_row[0]
        )
        if hashed:
            try:
                remaining = len(json.loads(hashed))
            except (json.JSONDecodeError, TypeError):
                remaining = 0
        else:
            remaining = len(
                [code for code in str(backup_codes or "").split(",") if code.strip()]
            )
    else:
        remaining = len(
            [code for code in str(backup_codes or "").split(",") if code.strip()]
        )
    admin_config = config.get("admin_ui", {})
    return AdminSecurityStatus(
        admin_id=admin_id,
        username=username,
        email=email,
        created_at=created_at,
        last_login=last_login,
        otp_required=bool(admin_config.get("require_otp", True)),
        otp_enabled=bool(totp_enabled),
        must_setup_otp=bool(must_setup_otp),
        backup_codes_remaining=remaining,
    )


def begin_otp_setup(db: Any, admin_id: int, current_password: str) -> AdminLoginResult:
    """Start a new admin OTP setup flow after password verification."""
    valid, row, message = _verify_admin_password(db, admin_id, current_password)
    if not valid or row is None:
        return AdminLoginResult(success=False, error=message)

    import pyotp
    from src.utils.encryption import encrypt_data as _encrypt

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    qr_uri = totp.provisioning_uri(name=row["username"], issuer_name="Plexichat Admin")
    # Store encrypted TOTP secret, not plaintext
    encrypted_secret = _encrypt(secret, context=f"admin_totp:{admin_id}")
    db.execute(
        """
        UPDATE admin_users
        SET totp_secret = NULL, totp_secret_encrypted = ?, totp_enabled = 0, must_setup_otp = 1,
            backup_codes = NULL, backup_codes_hash = NULL
        WHERE id = ?
        """,
        (encrypted_secret, admin_id),
    )
    return AdminLoginResult(
        success=True,
        user_id=admin_id,
        requires_otp_setup=True,
        otp_secret=secret,
        otp_qr_uri=qr_uri,
        challenge_token=_create_otp_challenge(admin_id, is_setup=True),
    )


def disable_otp(
    db: Any, admin_id: int, current_password: str, code: str
) -> Tuple[bool, str]:
    """Disable OTP for the current admin after password and OTP verification."""
    valid, row, message = _verify_admin_password(db, admin_id, current_password)
    if not valid or row is None:
        return False, message

    secret_row = db.fetch_one(
        "SELECT totp_secret, totp_secret_encrypted, totp_enabled, backup_codes, backup_codes_hash FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if not secret_row:
        return False, "Admin user not found"
    if isinstance(secret_row, dict):
        encrypted_secret = secret_row.get("totp_secret_encrypted")
        plaintext_secret = secret_row.get("totp_secret")
        totp_enabled = bool(secret_row["totp_enabled"])
        backup_codes_plaintext = secret_row.get("backup_codes") or ""
        backup_codes_hashed = secret_row.get("backup_codes_hash")
    else:
        plaintext_secret = secret_row[0]
        encrypted_secret = secret_row[1] if len(secret_row) > 1 else None
        totp_enabled = bool(secret_row[2])
        backup_codes_plaintext = secret_row[3] if len(secret_row) > 3 else ""
        backup_codes_hashed = secret_row[4] if len(secret_row) > 4 else None

    # Decrypt TOTP secret (prefer encrypted, fallback to plaintext legacy)
    secret = None
    if encrypted_secret:
        try:
            from src.utils.encryption import decrypt_data as _decrypt

            secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
        except Exception:
            secret = plaintext_secret
    else:
        secret = plaintext_secret

    if not totp_enabled or not secret:
        return False, "OTP is not enabled"

    import pyotp
    from src.utils.encryption import verify_password as _verify_pwd

    normalized = code.upper().replace("-", "")
    verified = pyotp.TOTP(secret).verify(code, valid_window=1)
    if not verified:
        # Try hashed backup codes first, then legacy plaintext
        if backup_codes_hashed:
            try:
                hashed_list = json.loads(backup_codes_hashed)
                for h in hashed_list:
                    if _verify_pwd(normalized.lower(), str(h)):
                        verified = True
                        break
            except (json.JSONDecodeError, TypeError):
                pass
        if not verified and backup_codes_plaintext:
            codes = [item for item in str(backup_codes_plaintext).split(",") if item]
            verified = normalized in codes
    if not verified:
        return False, "Invalid OTP or backup code"

    db.execute(
        """
        UPDATE admin_users
        SET totp_enabled = 0, totp_secret = NULL, totp_secret_encrypted = NULL,
            backup_codes = NULL, backup_codes_hash = NULL, must_setup_otp = 1
        WHERE id = ?
        """,
        (admin_id,),
    )
    return True, "OTP disabled"


def regenerate_backup_codes(
    db: Any, admin_id: int, current_password: str
) -> Tuple[bool, List[str], str]:
    """Regenerate admin backup codes after password verification."""
    valid, _row, message = _verify_admin_password(db, admin_id, current_password)
    if not valid:
        return False, [], message

    state_row = db.fetch_one(
        "SELECT totp_enabled FROM admin_users WHERE id = ?", (admin_id,)
    )
    if not state_row:
        return False, [], "Admin user not found"
    totp_enabled = (
        bool(state_row["totp_enabled"])
        if isinstance(state_row, dict)
        else bool(state_row[0])
    )
    if not totp_enabled:
        return False, [], "Enable OTP before generating backup codes"

    from src.utils.encryption import hash_password as _hash_pwd

    # Generate and hash backup codes (never store plaintext)
    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    hashed_codes = [_hash_pwd(c.replace("-", "").lower()) for c in backup_codes]
    db.execute(
        "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ? WHERE id = ?",
        (json.dumps(hashed_codes), admin_id),
    )
    return True, backup_codes, "Backup codes regenerated"
