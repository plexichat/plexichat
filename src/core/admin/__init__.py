"""
Admin module - Administrative functions for PlexiChat server.

This module provides:
- Auto-generated admin user with random password on first startup
- Admin login with rate limiting
- Optional OTP setup (configurable via require_otp setting)
- Feedback/ticket viewing and management
- Telemetry dashboard data
- Host restriction for admin access
- Separate listen addresses and allowed origins for admin panel

SECURITY WARNING: Disabling host_restriction allows ANYONE to access the admin
panel and view potentially sensitive user data including feedback, telemetry,
and system statistics. Only disable this if you have other security measures
in place (VPN, firewall, reverse proxy auth, etc.)

Configuration (in config.yaml):
    admin_ui:
      enabled: true
      path: /admin
      require_otp: true  # Set to false to disable 2FA requirement
      host_restriction:
        enabled: true
        allowed_hosts: ["127.0.0.1", "localhost", "::1"]
      allowed_origins: []  # Empty = use main CORS origins
      rate_limit:
        max_attempts: 5
        window_seconds: 300
        lockout_seconds: 900

Usage:
    from src.core import admin
    admin.setup(db, auth_module)
    
    # Admin login
    result = admin.login("admin", "password")
    if result.requires_otp_setup:
        # First login - must setup OTP (if require_otp is true)
        pass
"""

from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
import time
import secrets
import string
import os

import utils.logger as logger
import utils.config as config

_db: Any = None
_auth: Any = None
_setup_complete = False

# Rate limiting for admin login
_login_attempts: Dict[str, List[float]] = {}  # IP -> list of attempt timestamps
_lockouts: Dict[str, float] = {}  # IP -> lockout until timestamp


@dataclass
class FeedbackTicket:
    """A feedback/support ticket."""
    id: int
    user_id: int
    username: str
    content: str
    category: Optional[str]
    rating: Optional[int]
    status: str  # 'open', 'in_progress', 'resolved', 'closed'
    created_at: int
    resolved_at: Optional[int]
    resolved_by: Optional[int]
    internal_notes: Optional[str]


@dataclass
class AdminNote:
    """An internal admin note on a ticket."""
    id: int
    ticket_id: int
    admin_id: int
    admin_username: str
    content: str
    created_at: int


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


def setup(db: Any, auth_module: Optional[Any] = None) -> None:
    """Initialize the admin module."""
    global _db, _auth, _setup_complete

    _db = db
    _auth = auth_module
    _setup_complete = True  # Set before _create_tables() since it calls _get_db()
    _create_tables()
    _ensure_admin_user()


def _create_tables() -> None:
    """Create admin tables if they don't exist."""
    db = _get_db()

    # Create admin_users table (separate from regular users)
    admin_schema = """
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT,
        totp_secret TEXT,
        totp_enabled INTEGER DEFAULT 0,
        backup_codes TEXT,
        created_at INTEGER NOT NULL,
        last_login INTEGER,
        must_setup_otp INTEGER DEFAULT 1
    )
    """
    db.execute(db.convert_schema(admin_schema) if hasattr(db, 'convert_schema') else admin_schema)

    # Create admin_sessions table
    session_schema = """
    CREATE TABLE IF NOT EXISTS admin_sessions (
        id INTEGER PRIMARY KEY,
        admin_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        FOREIGN KEY (admin_id) REFERENCES admin_users(id)
    )
    """
    db.execute(db.convert_schema(session_schema) if hasattr(db, 'convert_schema') else session_schema)

    # Ensure feedback table exists
    feedback_schema = """
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        category TEXT,
        rating INTEGER,
        created_at INTEGER NOT NULL,
        status TEXT DEFAULT 'open',
        resolved_at INTEGER,
        resolved_by INTEGER,
        internal_notes TEXT,
        FOREIGN KEY (user_id) REFERENCES auth_users(id)
    )
    """
    db.execute(db.convert_schema(feedback_schema) if hasattr(db, 'convert_schema') else feedback_schema)

    # Create admin_notes table
    notes_schema = """
    CREATE TABLE IF NOT EXISTS admin_notes (
        id INTEGER PRIMARY KEY,
        ticket_id INTEGER NOT NULL,
        admin_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES feedback(id),
        FOREIGN KEY (admin_id) REFERENCES admin_users(id)
    )
    """
    db.execute(db.convert_schema(notes_schema) if hasattr(db, 'convert_schema') else notes_schema)

    db.execute("CREATE INDEX IF NOT EXISTS idx_admin_notes_ticket ON admin_notes(ticket_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token)")


def _generate_password(length: int = 24) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _ensure_admin_user() -> None:
    """Ensure admin user exists, create with random password if not."""
    db = _get_db()

    # Check if admin user exists
    row = db.fetch_one("SELECT id FROM admin_users WHERE username = ?", ("admin",))
    if row:
        return

    # Generate random password
    password = _generate_password()

    # Hash password using auth module if available, otherwise use basic hash
    try:
        from src.utils.encryption import hash_password
        password_hash = hash_password(password)
    except ImportError:
        import hashlib
        password_hash = hashlib.sha256(password.encode()).hexdigest()

    # Generate snowflake ID
    try:
        from src.utils.encryption import generate_snowflake_id
        admin_id = generate_snowflake_id()
    except ImportError:
        admin_id = int(time.time() * 1000000)

    now = int(time.time() * 1000)

    # Create admin user
    db.execute(
        """INSERT INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, "admin", password_hash, "admin@example.com", now, 1)
    )

    # Save credentials to file
    _save_admin_credentials(password)

    logger.info("Created admin user with random password")


def _save_admin_credentials(password: str) -> None:
    """Save admin credentials to a secure file."""
    home_dir = Path.home() / ".plexichat"
    creds_file = home_dir / "admin_credentials.txt"

    # Ensure directory exists
    home_dir.mkdir(parents=True, exist_ok=True)

    content = f"""PlexiChat Admin Credentials
============================
Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

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

    # Write with restricted permissions
    creds_file.write_text(content)
    try:
        os.chmod(creds_file, 0o600)  # Owner read/write only
    except Exception:
        pass  # Windows doesn't support chmod the same way

    logger.warning(f"Admin credentials saved to: {creds_file}")
    logger.warning("IMPORTANT: Delete this file after noting the credentials!")


def is_setup() -> bool:
    """Check if admin module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("Admin not initialized. Call admin.setup(db) first.")
    if _db is None:
        raise RuntimeError("Admin database not set")
    return _db


def _check_rate_limit(ip: str, max_attempts: int = 5, window_seconds: int = 300, lockout_seconds: int = 900) -> Tuple[bool, Optional[int]]:
    """Check if IP is rate limited. Returns (allowed, seconds_until_unlock)."""
    now = time.time() * 1000
    window_ms = window_seconds * 1000
    lockout_ms = lockout_seconds * 1000

    # Check lockout
    if ip in _lockouts:
        if now < _lockouts[ip]:
            return False, int((_lockouts[ip] - now) / 1000)
        else:
            del _lockouts[ip]

    # Clean old attempts
    if ip in _login_attempts:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < window_ms]

    # Check attempt count
    attempts = _login_attempts.get(ip, [])
    if len(attempts) >= max_attempts:
        _lockouts[ip] = now + lockout_ms
        return False, lockout_seconds

    return True, None


def _record_login_attempt(ip: str) -> None:
    """Record a failed login attempt."""
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(time.time() * 1000)


def _clear_login_attempts(ip: str) -> None:
    """Clear login attempts after successful login."""
    if ip in _login_attempts:
        del _login_attempts[ip]
    if ip in _lockouts:
        del _lockouts[ip]


def _is_otp_required() -> bool:
    """Check if OTP is required based on config."""
    admin_config = config.get("admin_ui", {})
    return admin_config.get("require_otp", True)


def login(username: str, password: str, ip: str = "unknown") -> AdminLoginResult:
    """Authenticate admin user."""
    db = _get_db()

    # Get rate limit config
    admin_config = config.get("admin_ui", {})
    rate_config = admin_config.get("rate_limit", {})
    max_attempts = rate_config.get("max_attempts", 5)
    window_seconds = rate_config.get("window_seconds", 300)
    lockout_seconds = rate_config.get("lockout_seconds", 900)

    # Check rate limit
    allowed, wait_seconds = _check_rate_limit(ip, max_attempts, window_seconds, lockout_seconds)
    if not allowed:
        return AdminLoginResult(
            success=False,
            error=f"Too many login attempts. Try again in {wait_seconds} seconds."
        )

    # Get admin user
    row = db.fetch_one(
        "SELECT id, password_hash, totp_secret, totp_enabled, must_setup_otp FROM admin_users WHERE username = ?",
        (username,)
    )

    if not row:
        _record_login_attempt(ip)
        return AdminLoginResult(success=False, error="Invalid credentials")

    if isinstance(row, dict):
        admin_id = row["id"]
        password_hash = row["password_hash"]
        totp_secret = row["totp_secret"]
        totp_enabled = bool(row["totp_enabled"])
        must_setup_otp = bool(row["must_setup_otp"])
    else:
        admin_id, password_hash, totp_secret, totp_enabled, must_setup_otp = row
        totp_enabled = bool(totp_enabled)
        must_setup_otp = bool(must_setup_otp)

    # Verify password
    try:
        from src.utils.encryption import verify_password
        if not verify_password(password, password_hash):
            _record_login_attempt(ip)
            return AdminLoginResult(success=False, error="Invalid credentials")
    except ImportError:
        import hashlib
        if hashlib.sha256(password.encode()).hexdigest() != password_hash:
            _record_login_attempt(ip)
            return AdminLoginResult(success=False, error="Invalid credentials")

    _clear_login_attempts(ip)

    # Check if OTP is required by config
    otp_required = _is_otp_required()

    # If OTP is not required, skip OTP setup/verification
    if not otp_required:
        # Clear the must_setup_otp flag if it was set
        if must_setup_otp:
            db.execute("UPDATE admin_users SET must_setup_otp = 0 WHERE id = ?", (admin_id,))

        # Create session directly
        token = _create_session(admin_id)
        db.execute("UPDATE admin_users SET last_login = ? WHERE id = ?", (int(time.time() * 1000), admin_id))
        return AdminLoginResult(success=True, token=token, user_id=admin_id)

    # OTP is required - check if setup is needed (first login)
    if must_setup_otp or (not totp_enabled and not totp_secret):
        # Generate OTP secret
        import pyotp
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        qr_uri = totp.provisioning_uri(name=username, issuer_name="PlexiChat Admin")

        # Store secret temporarily (not enabled yet)
        db.execute(
            "UPDATE admin_users SET totp_secret = ? WHERE id = ?",
            (secret, admin_id)
        )

        # Generate challenge token for OTP setup
        challenge = secrets.token_urlsafe(32)

        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_setup=True,
            otp_secret=secret,
            otp_qr_uri=qr_uri,
            challenge_token=challenge
        )

    # OTP is enabled, require verification
    if totp_enabled:
        challenge = secrets.token_urlsafe(32)
        # Store challenge temporarily (in memory or session)
        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_verify=True,
            challenge_token=challenge
        )

    # Should not reach here - OTP should always be required when enabled
    return AdminLoginResult(success=False, error="OTP setup required")


def verify_otp_setup(admin_id: int, code: str) -> AdminLoginResult:
    """Verify OTP code during setup and enable OTP."""
    db = _get_db()

    logger.debug(f"verify_otp_setup called with admin_id={admin_id}, code={code}")

    row = db.fetch_one(
        "SELECT totp_secret FROM admin_users WHERE id = ?",
        (admin_id,)
    )

    logger.debug(f"DB row result: {row}")

    if not row:
        logger.warning(f"Admin user {admin_id} not found")
        return AdminLoginResult(success=False, error="Admin user not found")

    secret = row["totp_secret"] if isinstance(row, dict) else row[0]

    logger.debug(f"Secret found: {bool(secret)}, length: {len(secret) if secret else 0}")

    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")

    # Verify code
    import pyotp
    totp = pyotp.TOTP(secret)
    expected = totp.now()
    logger.debug(f"Expected OTP: {expected}, Provided: {code}")

    if not totp.verify(code, valid_window=1):
        logger.warning(f"OTP verification failed for admin {admin_id}")
        return AdminLoginResult(success=False, error="Invalid OTP code")

    # Enable OTP and clear must_setup flag
    db.execute(
        "UPDATE admin_users SET totp_enabled = 1, must_setup_otp = 0 WHERE id = ?",
        (admin_id,)
    )

    # Generate backup codes
    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    db.execute(
        "UPDATE admin_users SET backup_codes = ? WHERE id = ?",
        (",".join(backup_codes), admin_id)
    )

    # Create session
    token = _create_session(admin_id)

    return AdminLoginResult(
        success=True,
        token=token,
        user_id=admin_id
    )


def verify_otp(admin_id: int, code: str) -> AdminLoginResult:
    """Verify OTP code for login."""
    db = _get_db()

    row = db.fetch_one(
        "SELECT totp_secret, backup_codes FROM admin_users WHERE id = ?",
        (admin_id,)
    )

    if not row:
        return AdminLoginResult(success=False, error="Admin user not found")

    if isinstance(row, dict):
        secret = row["totp_secret"]
        backup_codes = row["backup_codes"]
    else:
        secret, backup_codes = row

    if not secret:
        return AdminLoginResult(success=False, error="OTP not configured")

    # Try TOTP code first
    import pyotp
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        token = _create_session(admin_id)
        db.execute("UPDATE admin_users SET last_login = ? WHERE id = ?", (int(time.time() * 1000), admin_id))
        return AdminLoginResult(success=True, token=token, user_id=admin_id)

    # Try backup code
    if backup_codes:
        codes = backup_codes.split(",")
        code_upper = code.upper().replace("-", "")
        if code_upper in codes:
            codes.remove(code_upper)
            db.execute(
                "UPDATE admin_users SET backup_codes = ?, last_login = ? WHERE id = ?",
                (",".join(codes), int(time.time() * 1000), admin_id)
            )
            token = _create_session(admin_id)
            return AdminLoginResult(success=True, token=token, user_id=admin_id)

    return AdminLoginResult(success=False, error="Invalid OTP code")


def _create_session(admin_id: int, expires_hours: int = 8) -> str:
    """Create admin session and return token."""
    db = _get_db()

    token = secrets.token_urlsafe(32)
    now = int(time.time() * 1000)
    expires = now + (expires_hours * 3600 * 1000)

    try:
        from src.utils.encryption import generate_snowflake_id
        session_id = generate_snowflake_id()
    except ImportError:
        session_id = int(time.time() * 1000000)

    db.execute(
        """INSERT INTO admin_sessions (id, admin_id, token, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, admin_id, token, now, expires)
    )

    return token


def validate_session(token: str) -> Optional[int]:
    """Validate admin session token. Returns admin_id or None."""
    db = _get_db()

    now = int(time.time() * 1000)
    row = db.fetch_one(
        "SELECT admin_id FROM admin_sessions WHERE token = ? AND expires_at > ?",
        (token, now)
    )

    if row:
        return row["admin_id"] if isinstance(row, dict) else row[0]
    return None


def logout(token: str) -> bool:
    """Invalidate admin session."""
    db = _get_db()
    db.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
    return True


def get_feedback_tickets(
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[FeedbackTicket]:
    """Get feedback tickets with optional status filter."""
    db = _get_db()

    if status_filter:
        rows = db.fetch_all(
            """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                      COALESCE(f.status, 'open') as status, f.created_at, 
                      f.resolved_at, f.resolved_by, f.internal_notes
               FROM feedback f
               LEFT JOIN auth_users u ON f.user_id = u.id
               WHERE COALESCE(f.status, 'open') = ?
               ORDER BY f.created_at DESC
               LIMIT ? OFFSET ?""",
            (status_filter, limit, offset)
        )
    else:
        rows = db.fetch_all(
            """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                      COALESCE(f.status, 'open') as status, f.created_at,
                      f.resolved_at, f.resolved_by, f.internal_notes
               FROM feedback f
               LEFT JOIN auth_users u ON f.user_id = u.id
               ORDER BY f.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )

    tickets = []
    for row in rows:
        if isinstance(row, dict):
            tickets.append(FeedbackTicket(
                id=row["id"], user_id=row["user_id"], username=row["username"] or "Unknown",
                content=row["content"], category=row["category"], rating=row["rating"],
                status=row["status"], created_at=row["created_at"], resolved_at=row["resolved_at"],
                resolved_by=row["resolved_by"], internal_notes=row["internal_notes"]
            ))
        else:
            tickets.append(FeedbackTicket(
                id=row[0], user_id=row[1], username=row[2] or "Unknown",
                content=row[3], category=row[4], rating=row[5], status=row[6],
                created_at=row[7], resolved_at=row[8], resolved_by=row[9], internal_notes=row[10]
            ))
    return tickets


def get_ticket(ticket_id: int) -> Optional[FeedbackTicket]:
    """Get a single feedback ticket by ID."""
    db = _get_db()
    row = db.fetch_one(
        """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                  COALESCE(f.status, 'open') as status, f.created_at,
                  f.resolved_at, f.resolved_by, f.internal_notes
           FROM feedback f
           LEFT JOIN auth_users u ON f.user_id = u.id
           WHERE f.id = ?""",
        (ticket_id,)
    )
    if not row:
        return None

    if isinstance(row, dict):
        return FeedbackTicket(
            id=row["id"], user_id=row["user_id"], username=row["username"] or "Unknown",
            content=row["content"], category=row["category"], rating=row["rating"],
            status=row["status"], created_at=row["created_at"], resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"], internal_notes=row["internal_notes"]
        )
    return FeedbackTicket(
        id=row[0], user_id=row[1], username=row[2] or "Unknown",
        content=row[3], category=row[4], rating=row[5], status=row[6],
        created_at=row[7], resolved_at=row[8], resolved_by=row[9], internal_notes=row[10]
    )


def update_ticket_status(ticket_id: int, status: str, admin_id: int) -> bool:
    """Update the status of a feedback ticket."""
    db = _get_db()

    valid_statuses = ['open', 'in_progress', 'resolved', 'closed']
    if status not in valid_statuses:
        return False

    resolved_at = int(time.time() * 1000) if status in ['resolved', 'closed'] else None
    resolved_by = admin_id if status in ['resolved', 'closed'] else None

    db.execute(
        """UPDATE feedback SET status = ?, resolved_at = ?, resolved_by = ?
           WHERE id = ?""",
        (status, resolved_at, resolved_by, ticket_id)
    )
    return True


def add_internal_note(ticket_id: int, admin_id: int, content: str) -> Optional[AdminNote]:
    """Add an internal note to a ticket."""
    db = _get_db()

    try:
        from src.utils.encryption import generate_snowflake_id
        note_id = generate_snowflake_id()
    except ImportError:
        note_id = int(time.time() * 1000000)

    now = int(time.time() * 1000)

    db.execute(
        """INSERT INTO admin_notes (id, ticket_id, admin_id, content, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (note_id, ticket_id, admin_id, content, now)
    )

    # Get admin username
    row = db.fetch_one("SELECT username FROM admin_users WHERE id = ?", (admin_id,))
    username = (row["username"] if isinstance(row, dict) else row[0]) if row else "Unknown"

    return AdminNote(
        id=note_id, ticket_id=ticket_id, admin_id=admin_id,
        admin_username=username, content=content, created_at=now
    )


def get_ticket_notes(ticket_id: int) -> List[AdminNote]:
    """Get all internal notes for a ticket."""
    db = _get_db()

    rows = db.fetch_all(
        """SELECT n.id, n.ticket_id, n.admin_id, u.username, n.content, n.created_at
           FROM admin_notes n
           LEFT JOIN admin_users u ON n.admin_id = u.id
           WHERE n.ticket_id = ?
           ORDER BY n.created_at ASC""",
        (ticket_id,)
    )

    notes = []
    for row in rows:
        if isinstance(row, dict):
            notes.append(AdminNote(
                id=row["id"], ticket_id=row["ticket_id"], admin_id=row["admin_id"],
                admin_username=row["username"] or "Unknown", content=row["content"],
                created_at=row["created_at"]
            ))
        else:
            notes.append(AdminNote(
                id=row[0], ticket_id=row[1], admin_id=row[2],
                admin_username=row[3] or "Unknown", content=row[4], created_at=row[5]
            ))
    return notes


def get_ticket_counts() -> Dict[str, int]:
    """Get counts of tickets by status."""
    db = _get_db()

    counts = {'open': 0, 'in_progress': 0, 'resolved': 0, 'closed': 0, 'total': 0}

    rows = db.fetch_all(
        """SELECT COALESCE(status, 'open') as status, COUNT(*) as count
           FROM feedback GROUP BY COALESCE(status, 'open')"""
    )

    for row in rows:
        status = row["status"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        if status in counts:
            counts[status] = count
        counts['total'] += count

    return counts


def check_host_restriction(client_ip: str, allowed_hosts: List[str]) -> bool:
    """Check if client IP is allowed to access admin UI."""
    if not allowed_hosts:
        return True

    # Normalize localhost variations
    localhost_variants = ['127.0.0.1', 'localhost', '::1']

    for allowed in allowed_hosts:
        if allowed in localhost_variants and client_ip in localhost_variants:
            return True
        if client_ip == allowed:
            return True
        # Support CIDR notation (basic)
        if '/' in allowed:
            prefix = allowed.split('/')[0].rsplit('.', 1)[0]
            if client_ip.startswith(prefix):
                return True

    return False


# ==================== Hash Reports (Content Moderation) ====================

@dataclass
class HashReport:
    """A content hash report for moderation."""
    id: int
    hash_value: str
    reporter_id: int
    reporter_username: Optional[str]
    reason: str
    details: Optional[str]
    status: str  # 'pending', 'reviewed', 'blocked', 'cleared'
    reported_at: int
    reviewed_at: Optional[int]
    reviewed_by: Optional[int]
    admin_notes: Optional[str]
    # New fields for enhanced reporting
    phash_value: Optional[str] = None
    uploader_id: Optional[int] = None
    message_id: Optional[int] = None
    attachment_url: Optional[str] = None
    block_uploader: bool = False


@dataclass
class BlockedHash:
    """A blocked content hash."""
    hash_value: str
    reason: str
    blocked_at: int
    blocked_by: Optional[int]
    auto_blocked: bool
    hash_type: str = "sha256"
    phash_threshold: int = 10


@dataclass
class BlockedUser:
    """A user blocked from uploading media."""
    user_id: int
    username: Optional[str]
    reason: str
    blocked_at: int
    blocked_by: Optional[int]
    expires_at: Optional[int]


def get_hash_reports(
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[HashReport]:
    """Get hash reports for admin review."""
    db = _get_db()

    query = """SELECT r.id, r.hash_value, r.reporter_id, u.username, r.reason, 
                      r.details, r.status, r.reported_at, r.reviewed_at, 
                      r.reviewed_by, r.admin_notes, r.phash_value, r.uploader_id,
                      r.message_id, r.attachment_url, r.block_uploader
               FROM media_hash_reports r
               LEFT JOIN auth_users u ON r.reporter_id = u.id"""

    if status_filter:
        query += " WHERE r.status = ?"
        query += " ORDER BY r.reported_at DESC LIMIT ? OFFSET ?"
        rows = db.fetch_all(query, (status_filter, limit, offset))
    else:
        query += " ORDER BY r.reported_at DESC LIMIT ? OFFSET ?"
        rows = db.fetch_all(query, (limit, offset))

    reports = []
    for row in rows:
        if isinstance(row, dict):
            reports.append(HashReport(
                id=row["id"], hash_value=row["hash_value"],
                reporter_id=row["reporter_id"], reporter_username=row["username"],
                reason=row["reason"], details=row["details"], status=row["status"],
                reported_at=row["reported_at"], reviewed_at=row["reviewed_at"],
                reviewed_by=row["reviewed_by"], admin_notes=row["admin_notes"],
                phash_value=row.get("phash_value"),
                uploader_id=row.get("uploader_id"),
                message_id=row.get("message_id"),
                attachment_url=row.get("attachment_url"),
                block_uploader=bool(row.get("block_uploader", 0))
            ))
        else:
            reports.append(HashReport(
                id=row[0], hash_value=row[1], reporter_id=row[2],
                reporter_username=row[3], reason=row[4], details=row[5],
                status=row[6], reported_at=row[7], reviewed_at=row[8],
                reviewed_by=row[9], admin_notes=row[10],
                phash_value=row[11] if len(row) > 11 else None,
                uploader_id=row[12] if len(row) > 12 else None,
                message_id=row[13] if len(row) > 13 else None,
                attachment_url=row[14] if len(row) > 14 else None,
                block_uploader=bool(row[15]) if len(row) > 15 else False
            ))

    return reports


def get_hash_report_counts() -> Dict[str, int]:
    """Get counts of hash reports by status."""
    db = _get_db()

    counts = {'pending': 0, 'reviewed': 0, 'blocked': 0, 'cleared': 0, 'total': 0}

    try:
        rows = db.fetch_all(
            "SELECT status, COUNT(*) as count FROM media_hash_reports GROUP BY status"
        )

        for row in rows:
            status = row["status"] if isinstance(row, dict) else row[0]
            count = row["count"] if isinstance(row, dict) else row[1]
            if status in counts:
                counts[status] = count
            counts['total'] += count
    except Exception:
        # Table may not exist yet
        pass

    return counts


def get_blocked_hashes(limit: int = 100, offset: int = 0) -> List[BlockedHash]:
    """Get list of blocked hashes."""
    db = _get_db()

    try:
        rows = db.fetch_all(
            """SELECT hash_value, reason, blocked_at, blocked_by, auto_blocked,
                      hash_type, phash_threshold
               FROM media_blocked_hashes
               ORDER BY blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )

        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(BlockedHash(
                    hash_value=row["hash_value"],
                    reason=row["reason"],
                    blocked_at=row["blocked_at"],
                    blocked_by=row["blocked_by"],
                    auto_blocked=bool(row["auto_blocked"]),
                    hash_type=row.get("hash_type", "sha256"),
                    phash_threshold=row.get("phash_threshold", 10)
                ))
            else:
                result.append(BlockedHash(
                    hash_value=row[0], reason=row[1], blocked_at=row[2],
                    blocked_by=row[3], auto_blocked=bool(row[4]),
                    hash_type=row[5] if len(row) > 5 else "sha256",
                    phash_threshold=row[6] if len(row) > 6 else 10
                ))
        return result
    except Exception:
        return []


def get_blocked_hash_count() -> int:
    """Get count of blocked hashes."""
    db = _get_db()
    try:
        row = db.fetch_one("SELECT COUNT(*) as count FROM media_blocked_hashes")
        return row["count"] if isinstance(row, dict) else row[0] if row else 0
    except Exception:
        return 0


def review_hash_report(
    report_id: int,
    admin_id: int,
    action: str,
    notes: Optional[str] = None
) -> bool:
    """
    Review a hash report.
    
    Args:
        report_id: Report ID
        admin_id: Admin user ID
        action: 'block', 'clear', or 'dismiss'
        notes: Admin notes
    """
    db = _get_db()
    now = int(time.time() * 1000)

    # Get report
    row = db.fetch_one(
        "SELECT hash_value FROM media_hash_reports WHERE id = ?",
        (report_id,)
    )
    if not row:
        return False

    hash_value = row["hash_value"] if isinstance(row, dict) else row[0]

    if action == "block":
        # Block the hash
        try:
            db.execute(
                """INSERT OR REPLACE INTO media_blocked_hashes 
                   (hash_value, reason, blocked_at, blocked_by, auto_blocked)
                   VALUES (?, ?, ?, ?, 0)""",
                (hash_value, notes or "Blocked by admin", now, admin_id)
            )
        except Exception as e:
            logger.error(f"Failed to block hash: {e}")
            return False
        status = "blocked"
    elif action == "clear":
        status = "cleared"
    else:
        status = "reviewed"

    db.execute(
        """UPDATE media_hash_reports 
           SET status = ?, reviewed_at = ?, reviewed_by = ?, admin_notes = ?
           WHERE id = ?""",
        (status, now, admin_id, notes, report_id)
    )

    return True


def unblock_hash(hash_value: str) -> bool:
    """Unblock a hash."""
    db = _get_db()
    try:
        db.execute(
            "DELETE FROM media_blocked_hashes WHERE hash_value = ?",
            (hash_value,)
        )
        return True
    except Exception as e:
        logger.error(f"Failed to unblock hash: {e}")
        return False


def block_hash(
    hash_value: str,
    reason: str,
    admin_id: int,
    hash_type: str = "sha256",
    phash_threshold: int = 10
) -> bool:
    """Manually block a hash."""
    db = _get_db()
    now = int(time.time() * 1000)

    try:
        db.execute(
            """INSERT OR REPLACE INTO media_blocked_hashes 
               (hash_value, hash_type, phash_threshold, reason, blocked_at, blocked_by, auto_blocked)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (hash_value, hash_type, phash_threshold, reason, now, admin_id)
        )
        return True
    except Exception as e:
        logger.error(f"Failed to block hash: {e}")
        return False


# ==================== User Blocking (Media Uploads) ====================

def get_blocked_users(limit: int = 100, offset: int = 0) -> List[BlockedUser]:
    """Get list of users blocked from uploading media."""
    db = _get_db()

    try:
        rows = db.fetch_all(
            """SELECT b.user_id, u.username, b.reason, b.blocked_at, b.blocked_by, b.expires_at
               FROM media_blocked_users b
               LEFT JOIN auth_users u ON b.user_id = u.id
               ORDER BY b.blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )

        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(BlockedUser(
                    user_id=row["user_id"],
                    username=row.get("username"),
                    reason=row["reason"],
                    blocked_at=row["blocked_at"],
                    blocked_by=row["blocked_by"],
                    expires_at=row.get("expires_at")
                ))
            else:
                result.append(BlockedUser(
                    user_id=row[0], username=row[1], reason=row[2],
                    blocked_at=row[3], blocked_by=row[4],
                    expires_at=row[5] if len(row) > 5 else None
                ))
        return result
    except Exception:
        return []


def block_user(
    user_id: int,
    reason: str,
    admin_id: int,
    duration_hours: Optional[int] = None
) -> bool:
    """Block a user from uploading media."""
    db = _get_db()
    now = int(time.time() * 1000)
    expires_at = None
    if duration_hours:
        expires_at = now + (duration_hours * 3600 * 1000)

    try:
        db.execute(
            """INSERT OR REPLACE INTO media_blocked_users 
               (user_id, reason, blocked_at, blocked_by, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, reason, now, admin_id, expires_at)
        )
        logger.info(f"Admin {admin_id} blocked user {user_id} from uploads: {reason}")
        return True
    except Exception as e:
        logger.error(f"Failed to block user: {e}")
        return False


def unblock_user(user_id: int) -> bool:
    """Unblock a user from uploading media."""
    db = _get_db()
    try:
        db.execute(
            "DELETE FROM media_blocked_users WHERE user_id = ?",
            (user_id,)
        )
        return True
    except Exception as e:
        logger.error(f"Failed to unblock user: {e}")
        return False


__all__ = [
    'setup', 'is_setup', 'login', 'verify_otp_setup', 'verify_otp',
    'validate_session', 'logout',
    'get_feedback_tickets', 'get_ticket', 'update_ticket_status',
    'add_internal_note', 'get_ticket_notes', 'get_ticket_counts',
    'check_host_restriction', 'FeedbackTicket', 'AdminNote', 'AdminLoginResult',
    # Hash reports
    'get_hash_reports', 'get_hash_report_counts', 'get_blocked_hashes',
    'get_blocked_hash_count', 'review_hash_report', 'unblock_hash', 'block_hash',
    'HashReport', 'BlockedHash',
    # User blocking
    'get_blocked_users', 'block_user', 'unblock_user', 'BlockedUser',
]
