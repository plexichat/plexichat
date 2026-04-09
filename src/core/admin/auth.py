"""
Admin authentication and session management for Plexichat Admin.
"""

from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
import time
import secrets
import string
import hashlib
import threading
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


# Rate limiting for admin login
_login_attempts: Dict[str, List[float]] = {}  # IP -> list of attempt timestamps
_lockouts: Dict[str, float] = {}  # IP -> lockout until timestamp
_otp_challenges: Dict[str, Dict[str, Any]] = {}
_otp_lock = threading.Lock()


def _hash_admin_token(token: str) -> str:
    """Hash admin bearer tokens before persistence."""
    return "sha256$" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_otp_challenge(admin_id: int, is_setup: bool, ttl_seconds: int = 300) -> str:
    """Create a short-lived challenge token for OTP verification binding."""
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time() * 1000) + (ttl_seconds * 1000)
    with _otp_lock:
        _otp_challenges[token] = {
            "admin_id": admin_id,
            "is_setup": is_setup,
            "expires_at": expires_at,
        }
    return token


def _validate_otp_challenge(
    challenge_token: str, admin_id: int, is_setup: bool
) -> bool:
    """Validate OTP challenge token against admin and flow type."""
    now = int(time.time() * 1000)
    with _otp_lock:
        payload = _otp_challenges.get(challenge_token)
        if not payload:
            return False
        if payload["expires_at"] < now:
            _otp_challenges.pop(challenge_token, None)
            return False
        if payload["admin_id"] != admin_id or payload["is_setup"] != is_setup:
            return False
        return True


def _consume_otp_challenge(challenge_token: str) -> None:
    with _otp_lock:
        _otp_challenges.pop(challenge_token, None)


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


def ensure_admin_user(db: Any) -> None:
    """Ensure admin user exists, create with random password if not."""
    row = db.fetch_one("SELECT id FROM admin_users WHERE username = ?", ("admin",))
    if row:
        return
    password = _generate_password()
    import src.utils.encryption as encryption

    password_hash = encryption.hash_password(password)
    admin_id = encryption.generate_snowflake_id()
    now = int(time.time() * 1000)
    db.execute(
        """INSERT INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, "admin", password_hash, "admin@example.com", now, 1),
    )
    _save_admin_credentials(password)
    logger.info("Created admin user with random password using Argon2id")


def _check_rate_limit(
    ip: str,
    max_attempts: int = 5,
    window_seconds: int = 300,
    lockout_seconds: int = 900,
) -> Tuple[bool, Optional[int]]:
    now = time.time() * 1000
    if ip in _lockouts:
        if now < _lockouts[ip]:
            return False, int((_lockouts[ip] - now) / 1000)
        else:
            del _lockouts[ip]
    if ip in _login_attempts:
        _login_attempts[ip] = [
            t for t in _login_attempts[ip] if now - t < window_seconds * 1000
        ]
    attempts = _login_attempts.get(ip, [])
    if len(attempts) >= max_attempts:
        _lockouts[ip] = now + (lockout_seconds * 1000)
        return False, lockout_seconds
    return True, None


def login(
    db: Any, username: str, password: str, ip: str = "unknown"
) -> AdminLoginResult:
    """Authenticate admin user."""
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
        )

    row = db.fetch_one(
        "SELECT id, password_hash, totp_secret, totp_enabled, must_setup_otp FROM admin_users WHERE username = ?",
        (username,),
    )
    if not row:
        if ip not in _login_attempts:
            _login_attempts[ip] = []
        _login_attempts[ip].append(time.time() * 1000)
        return AdminLoginResult(success=False, error="Invalid credentials")

    if isinstance(row, dict):
        admin_id, password_hash, totp_secret, totp_enabled, must_setup_otp = (
            row["id"],
            row["password_hash"],
            row["totp_secret"],
            bool(row["totp_enabled"]),
            bool(row["must_setup_otp"]),
        )
    else:
        admin_id, password_hash, totp_secret, totp_enabled, must_setup_otp = (
            row[0],
            row[1],
            row[2],
            bool(row[3]),
            bool(row[4]),
        )

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
    else:
        if encryption.verify_password(password, password_hash):
            authenticated = True

    if not authenticated:
        if ip not in _login_attempts:
            _login_attempts[ip] = []
        _login_attempts[ip].append(time.time() * 1000)
        return AdminLoginResult(success=False, error="Invalid credentials")

    if ip in _login_attempts:
        del _login_attempts[ip]
    if ip in _lockouts:
        del _lockouts[ip]

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
        return AdminLoginResult(success=True, token=token, user_id=admin_id)

    if must_setup_otp or (not totp_enabled and not totp_secret):
        import pyotp

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        qr_uri = totp.provisioning_uri(name=username, issuer_name="Plexichat Admin")
        db.execute(
            "UPDATE admin_users SET totp_secret = ? WHERE id = ?", (secret, admin_id)
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

    return AdminLoginResult(success=False, error="OTP setup required")


def verify_otp_setup(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify OTP code during setup."""
    if not _validate_otp_challenge(challenge_token, admin_id, is_setup=True):
        return AdminLoginResult(success=False, error="Invalid or expired OTP challenge")

    row = db.fetch_one("SELECT totp_secret FROM admin_users WHERE id = ?", (admin_id,))
    if not row:
        return AdminLoginResult(success=False, error="Admin user not found")
    secret = row["totp_secret"] if isinstance(row, dict) else row[0]
    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")
    import pyotp

    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return AdminLoginResult(success=False, error="Invalid OTP code")
    db.execute(
        "UPDATE admin_users SET totp_enabled = 1, must_setup_otp = 0 WHERE id = ?",
        (admin_id,),
    )
    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    db.execute(
        "UPDATE admin_users SET backup_codes = ? WHERE id = ?",
        (",".join(backup_codes), admin_id),
    )
    _consume_otp_challenge(challenge_token)
    return AdminLoginResult(
        success=True, token=create_session(db, admin_id), user_id=admin_id
    )


def verify_otp(
    db: Any, admin_id: int, code: str, challenge_token: str
) -> AdminLoginResult:
    """Verify OTP code for login."""
    if not _validate_otp_challenge(challenge_token, admin_id, is_setup=False):
        return AdminLoginResult(success=False, error="Invalid or expired OTP challenge")

    row = db.fetch_one(
        "SELECT totp_secret, backup_codes FROM admin_users WHERE id = ?", (admin_id,)
    )
    if not row:
        return AdminLoginResult(success=False, error="Admin user not found")
    if isinstance(row, dict):
        secret, backup_codes = row["totp_secret"], row["backup_codes"]
    else:
        secret, backup_codes = row[0], row[1]
    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")
    import pyotp

    if pyotp.TOTP(secret).verify(code, valid_window=1):
        db.execute(
            "UPDATE admin_users SET last_login = ? WHERE id = ?",
            (int(time.time() * 1000), admin_id),
        )
        _consume_otp_challenge(challenge_token)
        return AdminLoginResult(
            success=True, token=create_session(db, admin_id), user_id=admin_id
        )
    if backup_codes:
        codes = backup_codes.split(",")
        if code.upper().replace("-", "") in codes:
            codes.remove(code.upper().replace("-", ""))
            db.execute(
                "UPDATE admin_users SET backup_codes = ?, last_login = ? WHERE id = ?",
                (",".join(codes), int(time.time() * 1000), admin_id),
            )
            _consume_otp_challenge(challenge_token)
            return AdminLoginResult(
                success=True, token=create_session(db, admin_id), user_id=admin_id
            )
    return AdminLoginResult(success=False, error="Invalid OTP code")


def create_session(db: Any, admin_id: int, expires_hours: int = 8) -> str:
    """Create admin session."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_admin_token(token)
    now = int(time.time() * 1000)
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
    now = int(time.time() * 1000)
    token_hash = _hash_admin_token(token)
    row = db.fetch_one(
        "SELECT id, admin_id, token FROM admin_sessions WHERE (token = ? OR token = ?) AND expires_at > ?",
        (token_hash, token, now),
    )
    if row:
        if isinstance(row, dict):
            session_id = row["id"]
            admin_id = row["admin_id"]
            stored_token = row["token"]
        else:
            session_id = row[0]
            admin_id = row[1]
            stored_token = row[2]

        # Backward compatibility: migrate old plaintext tokens on successful validation.
        if stored_token == token:
            try:
                db.execute(
                    "UPDATE admin_sessions SET token = ? WHERE id = ?",
                    (token_hash, session_id),
                )
            except Exception:
                pass
        return admin_id
    return None


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
    """Change admin password."""
    valid, _row, message = _verify_admin_password(db, admin_id, current_password)
    if not valid:
        return False, message
    import src.utils.encryption as encryption

    db.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (encryption.hash_password(new_password), admin_id),
    )
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

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    qr_uri = totp.provisioning_uri(name=row["username"], issuer_name="Plexichat Admin")
    db.execute(
        """
        UPDATE admin_users
        SET totp_secret = ?, totp_enabled = 0, must_setup_otp = 1, backup_codes = NULL
        WHERE id = ?
        """,
        (secret, admin_id),
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
        "SELECT totp_secret, totp_enabled, backup_codes FROM admin_users WHERE id = ?",
        (admin_id,),
    )
    if not secret_row:
        return False, "Admin user not found"
    if isinstance(secret_row, dict):
        secret = secret_row["totp_secret"]
        totp_enabled = bool(secret_row["totp_enabled"])
        backup_codes = secret_row["backup_codes"] or ""
    else:
        secret, totp_enabled, backup_codes = secret_row
        totp_enabled = bool(totp_enabled)
        backup_codes = backup_codes or ""

    if not totp_enabled or not secret:
        return False, "OTP is not enabled"

    import pyotp

    normalized = code.upper().replace("-", "")
    verified = pyotp.TOTP(secret).verify(code, valid_window=1)
    if not verified:
        codes = [item for item in str(backup_codes).split(",") if item]
        verified = normalized in codes
    if not verified:
        return False, "Invalid OTP or backup code"

    db.execute(
        """
        UPDATE admin_users
        SET totp_enabled = 0, totp_secret = NULL, backup_codes = NULL, must_setup_otp = 1
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

    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    db.execute(
        "UPDATE admin_users SET backup_codes = ? WHERE id = ?",
        (",".join(backup_codes), admin_id),
    )
    return True, backup_codes, "Backup codes regenerated"
