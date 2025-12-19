"""
Authentication Manager - Core authentication logic.

Handles user registration, login, sessions, 2FA, bots, and audit logging.
"""

import time
import json
from typing import Optional, List, Dict, Any, Tuple

import utils.config as config
import utils.logger as logger

from src.utils.encryption import generate_snowflake_id

from .models import (
    User, Session, Bot, Device, AuditEntry, TokenInfo,
    AuthResult, TwoFactorSetup, TwoFactorStatus, PasswordValidation,
    TwoFactorChallenge,
    AccountType, AuthStatus, AuditEventType
)
from .exceptions import (
    AuthError, InvalidCredentialsError, AccountLockedError, EmailNotVerifiedError, TokenExpiredError, TokenInvalidError,
    TwoFactorInvalidError, PermissionDeniedError,
    UserExistsError, UserNotFoundError, WeakPasswordError, BotLimitExceededError,
    InvalidUsernameError, InvalidEmailError
)
from .permissions import (
    DEFAULT_USER_PERMISSIONS, DEFAULT_BOT_PERMISSIONS, has_permission, validate_permissions, permissions_to_json, permissions_from_json
)
from .schema import create_tables
from .tokens import (
    create_session_token, create_bot_token, create_email_token,
    create_2fa_challenge_token, parse_token, verify_token_hash, hash_token
)
from .passwords import (
    hash_password, verify_password, validate_password as validate_pwd,
    validate_username, validate_email
)
from . import totp as totp_module

# Import cache functions for token caching (graceful degradation if Redis unavailable)
from src.core.database import (
    cache_get, cache_set, cache_delete, check_rate_limit, redis_available
)


class AuthManager:
    """
    Main authentication manager.
    
    Handles all authentication operations including registration, login,
    session management, 2FA, bot accounts, and audit logging.
    """

    def __init__(self, db, email_sender=None):
        """
        Initialize the auth manager.
        
        Args:
            db: Connected database instance
            email_sender: Optional email sender for verification emails
        """
        self.db = db
        self.email_sender = email_sender

        # In-memory cache for user lookups (reduces DB queries)
        self._user_cache: Dict[int, Tuple[Any, float]] = {}
        self._user_cache_ttl = 60.0  # 60 second TTL

        # Create tables if they don't exist
        logger.info("Initializing authentication module")
        create_tables(db)
        logger.info("Authentication tables ready")

    def _cache_get_user(self, user_id: int) -> Optional[Any]:
        """Get user from cache if not expired."""
        if user_id in self._user_cache:
            user, expires = self._user_cache[user_id]
            if time.time() < expires:
                return user
            del self._user_cache[user_id]
        return None

    def _cache_set_user(self, user_id: int, user: Any):
        """Set user in cache with TTL."""
        self._user_cache[user_id] = (user, time.time() + self._user_cache_ttl)

    def _cache_invalidate_user(self, user_id: int):
        """Invalidate user cache entry."""
        self._user_cache.pop(user_id, None)

    def _get_config(self, key: str, default: Any = None) -> Any:
        """Get auth configuration value."""
        auth_config = config.get("authentication", {})
        keys = key.split(".")
        value = auth_config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        return value if value is not None else default

    def _current_time(self) -> int:
        """Get current Unix timestamp."""
        return int(time.time() * 1000)

    def _log_audit(
        self,
        event_type: AuditEventType,
        user_id: Optional[int],
        success: bool,
        ip_address: Optional[str] = None,
        device_id: Optional[int] = None,
        details: Optional[Dict] = None
    ) -> None:
        """Log an audit event."""
        audit_id = generate_snowflake_id()
        details_json = json.dumps(details) if details else None

        self.db.execute(
            """INSERT INTO auth_audit_log 
               (id, user_id, event_type, ip_address, device_id, timestamp, details, success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, user_id, event_type.value, ip_address, device_id,
             self._current_time(), details_json, 1 if success else 0)
        )

    # === User Registration ===

    def register(
        self,
        username: str,
        email: str,
        password: str,
        device_info: Optional[Dict[str, str]] = None,
        ip_address: Optional[str] = None
    ) -> User:
        """Register a new user account."""
        logger.info(f"Registration attempt for username: {username}")

        # Validate username
        valid, issues = validate_username(username)
        if not valid:
            logger.warning(f"Registration failed - invalid username: {issues}")
            raise InvalidUsernameError(f"Invalid username: {', '.join(issues)}", issues)

        # Validate email
        if not validate_email(email):
            logger.warning("Registration failed - invalid email format")
            raise InvalidEmailError("Invalid email format")

        # Validate password
        pwd_validation = validate_pwd(password)
        if not pwd_validation.valid:
            logger.warning(f"Registration failed - weak password: {pwd_validation.issues}")
            raise WeakPasswordError(
                f"Password does not meet requirements: {', '.join(pwd_validation.issues)}",
                pwd_validation.issues
            )

        # Check if username exists
        existing = self.db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?",
            (username,)
        )
        if existing:
            logger.warning(f"Registration failed - username exists: {username}")
            raise UserExistsError("Username already taken", "username")

        # Check if email exists
        existing = self.db.fetch_one(
            "SELECT id FROM auth_users WHERE email = ?",
            (email,)
        )
        if existing:
            logger.warning(f"Registration failed - email exists: {email}")
            raise UserExistsError("Email already registered", "email")

        # Create user
        user_id = generate_snowflake_id()
        now = self._current_time()
        password_hash = hash_password(password)
        permissions = permissions_to_json(DEFAULT_USER_PERMISSIONS)

        require_verification = self._get_config("accounts.require_email_verification", False)

        self.db.execute(
            """INSERT INTO auth_users 
               (id, account_type, username, email, password_hash, permissions,
                created_at, updated_at, email_verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, AccountType.USER.value, username, email, password_hash,
             permissions, now, now, 0 if require_verification else 1)
        )

        logger.info(f"User registered successfully: {username} (ID: {user_id})")

        # Log audit
        self._log_audit(
            AuditEventType.REGISTER, user_id, True, ip_address,
            details={"username": username, "email": email}
        )

        # Track IP if provided
        if ip_address:
            self._track_ip(user_id, ip_address)

        # Track device if provided
        if device_info:
            self._track_device(user_id, device_info)

        # Send verification email if configured
        if require_verification and self.email_sender:
            self._send_verification_email(user_id, email)

        user = User(
            id=user_id,
            account_type=AccountType.USER,
            username=username,
            email=email,
            permissions=DEFAULT_USER_PERMISSIONS.copy(),
            created_at=now,
            updated_at=now,
            email_verified=not require_verification
        )

        return user

    def verify_email(self, token: str) -> bool:
        """Verify email with token."""
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            logger.warning("Email verification failed - invalid token format")
            return False

        token_record = self.db.fetch_one(
            """SELECT id, user_id, token_hash, expires_at, used, token_type
               FROM auth_email_tokens WHERE id = ?""",
            (parsed["id"],)
        )

        if not token_record:
            logger.warning("Email verification failed - token not found")
            return False

        if token_record["token_type"] != "verify_email":
            return False

        if token_record["used"]:
            logger.warning("Email verification failed - token already used")
            return False

        if token_record["expires_at"] < self._current_time():
            logger.warning("Email verification failed - token expired")
            return False

        if not verify_token_hash(parsed["secret"], token_record["token_hash"]):
            logger.warning("Email verification failed - invalid token")
            return False

        # Mark token as used
        self.db.execute(
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?",
            (parsed["id"],)
        )

        # Mark email as verified
        self.db.execute(
            "UPDATE auth_users SET email_verified = 1, updated_at = ? WHERE id = ?",
            (self._current_time(), token_record["user_id"])
        )

        logger.info(f"Email verified for user ID: {token_record['user_id']}")

        self._log_audit(
            AuditEventType.EMAIL_VERIFIED, token_record["user_id"], True
        )

        return True

    def resend_verification(self, email: str) -> bool:
        """Resend verification email."""
        if not self.email_sender:
            logger.warning("Cannot resend verification - email not configured")
            return False

        user = self.db.fetch_one(
            "SELECT id, email_verified FROM auth_users WHERE email = ?",
            (email,)
        )

        if not user:
            # Don't reveal if email exists
            return True

        if user["email_verified"]:
            return True

        return self._send_verification_email(user["id"], email)

    def _send_verification_email(self, user_id: int, email: str) -> bool:
        """Send verification email."""
        if not self.email_sender:
            return False

        # Create token
        token_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + 86400  # 24 hours

        full_token, token_hash = create_email_token(token_id)

        self.db.execute(
            """INSERT INTO auth_email_tokens
               (id, user_id, token_hash, token_type, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token_id, user_id, token_hash, "verify_email", now, expires_at)
        )

        # Send email
        subject = "Verify your email address"
        body = f"Click here to verify your email: {full_token}"

        try:
            self.email_sender.send(email, subject, body)
            logger.info(f"Verification email sent to: {email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send verification email: {e}")
            return False

    # === User Login ===

    def login(
        self,
        username: str,
        password: str,
        device_info: Optional[Dict[str, str]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> AuthResult:
        """Authenticate a user."""
        logger.info(f"Login attempt for: {username}")

        # Find user by username or email
        user_row = self.db.fetch_one(
            """SELECT id, account_type, username, email, password_hash, permissions,
                      created_at, updated_at, email_verified, account_locked,
                      locked_until, failed_login_attempts, last_login_at, totp_enabled,
                      totp_secret_encrypted, backup_codes_hash
               FROM auth_users WHERE username = ? OR email = ?""",
            (username, username)
        )

        if not user_row:
            logger.warning(f"Login failed - user not found: {username}")
            self._log_audit(
                AuditEventType.LOGIN_FAILED, None, False, ip_address,
                details={"username": username, "reason": "user_not_found"}
            )
            raise InvalidCredentialsError("Invalid username or password")

        user_id = user_row["id"]

        # Check if account is locked
        if user_row["account_locked"]:
            locked_until = user_row["locked_until"]
            if locked_until and locked_until > self._current_time():
                logger.warning(f"Login failed - account locked: {username}")
                raise AccountLockedError(
                    "Account is temporarily locked due to too many failed attempts",
                    locked_until
                )
            else:
                # Unlock account
                self.db.execute(
                    """UPDATE auth_users SET account_locked = 0, locked_until = NULL,
                       failed_login_attempts = 0 WHERE id = ?""",
                    (user_id,)
                )
                self._log_audit(AuditEventType.ACCOUNT_UNLOCKED, user_id, True, ip_address)

        # Verify password
        if not verify_password(password, user_row["password_hash"]):
            logger.warning(f"Login failed - wrong password: {username}")
            self._handle_failed_login(user_id, ip_address)
            raise InvalidCredentialsError("Invalid username or password")

        # Check email verification
        require_verification = self._get_config("accounts.require_email_verification", False)
        if require_verification and not user_row["email_verified"]:
            logger.warning(f"Login failed - email not verified: {username}")
            raise EmailNotVerifiedError("Please verify your email address")

        # Track device and IP
        device_id = None
        if device_info:
            device_id = self._track_device(user_id, device_info)
        if ip_address:
            self._track_ip(user_id, ip_address)

        # Check if 2FA is required
        if user_row["totp_enabled"]:
            logger.info(f"2FA required for: {username}")
            challenge = self._create_2fa_challenge(
                user_id, device_id, ip_address, user_agent
            )
            return AuthResult(
                status=AuthStatus.TWO_FACTOR_REQUIRED,
                challenge_token=challenge.token,
                methods=["totp", "backup_code"],
                expires_in=300
            )

        # Create session
        session = self._create_session(user_id, device_id, ip_address, user_agent)

        # Reset failed attempts and update last login
        self.db.execute(
            """UPDATE auth_users SET failed_login_attempts = 0, last_login_at = ?,
               updated_at = ? WHERE id = ?""",
            (self._current_time(), self._current_time(), user_id)
        )

        logger.info(f"Login successful: {username}")
        self._log_audit(
            AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
        )

        user = self._row_to_user(user_row)

        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user,
            session=session
        )

    def _handle_failed_login(self, user_id: int, ip_address: Optional[str]) -> None:
        """Handle a failed login attempt."""
        max_attempts = self._get_config("security.max_failed_attempts", 5)
        lockout_minutes = self._get_config("security.lockout_duration_minutes", 15)

        # Increment failed attempts
        self.db.execute(
            "UPDATE auth_users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,)
        )

        # Check if should lock
        user = self.db.fetch_one(
            "SELECT failed_login_attempts FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if user and user["failed_login_attempts"] >= max_attempts:
            locked_until = self._current_time() + (lockout_minutes * 60)
            self.db.execute(
                "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
                (locked_until, user_id)
            )
            logger.warning(f"Account locked due to failed attempts: {user_id}")
            self._log_audit(
                AuditEventType.ACCOUNT_LOCKED, user_id, True, ip_address,
                details={"reason": "max_failed_attempts"}
            )

        self._log_audit(
            AuditEventType.LOGIN_FAILED, user_id, False, ip_address,
            details={"reason": "wrong_password"}
        )

    def complete_2fa(self, challenge_token: str, code: str) -> AuthResult:
        """Complete 2FA challenge."""
        parsed = parse_token(challenge_token)
        if not parsed or parsed["token_type"] != "2fa":
            logger.warning("2FA completion failed - invalid challenge token")
            raise TokenInvalidError("Invalid challenge token")

        challenge = self.db.fetch_one(
            """SELECT id, user_id, token_hash, device_id, ip_address, user_agent,
                      expires_at, used
               FROM auth_2fa_challenges WHERE id = ?""",
            (parsed["id"],)
        )

        if not challenge:
            raise TokenInvalidError("Challenge not found")

        if challenge["used"]:
            raise TokenInvalidError("Challenge already used")

        if challenge["expires_at"] < self._current_time():
            raise TokenExpiredError("Challenge expired")

        if not verify_token_hash(parsed["secret"], challenge["token_hash"]):
            raise TokenInvalidError("Invalid challenge token")

        user_id = challenge["user_id"]

        # Get user's TOTP secret
        user = self.db.fetch_one(
            "SELECT totp_secret_encrypted, backup_codes_hash FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        # Try TOTP code first
        is_backup = False
        if user["totp_secret_encrypted"]:
            secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
            if totp_module.verify_totp_code(secret, code):
                logger.info(f"2FA verified with TOTP for user: {user_id}")
            else:
                # Try backup code
                if user["backup_codes_hash"]:
                    backup_hashes = json.loads(user["backup_codes_hash"])
                    valid, index = totp_module.verify_backup_code(code, backup_hashes)
                    if valid:
                        is_backup = True
                        # Remove used backup code
                        backup_hashes.pop(index)
                        self.db.execute(
                            "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
                            (json.dumps(backup_hashes), user_id)
                        )
                        logger.info(f"2FA verified with backup code for user: {user_id}")
                        self._log_audit(
                            AuditEventType.TWO_FACTOR_BACKUP_USED, user_id, True,
                            challenge["ip_address"]
                        )
                    else:
                        logger.warning(f"2FA failed - invalid code for user: {user_id}")
                        raise TwoFactorInvalidError("Invalid 2FA code")
                else:
                    raise TwoFactorInvalidError("Invalid 2FA code")
        else:
            raise TwoFactorInvalidError("2FA not configured")

        # Mark challenge as used
        self.db.execute(
            "UPDATE auth_2fa_challenges SET used = 1 WHERE id = ?",
            (parsed["id"],)
        )

        # Create session
        session = self._create_session(
            user_id, challenge["device_id"],
            challenge["ip_address"], challenge["user_agent"]
        )

        # Update last login
        self.db.execute(
            """UPDATE auth_users SET failed_login_attempts = 0, last_login_at = ?,
               updated_at = ? WHERE id = ?""",
            (self._current_time(), self._current_time(), user_id)
        )

        self._log_audit(
            AuditEventType.LOGIN_SUCCESS, user_id, True,
            challenge["ip_address"], challenge["device_id"],
            details={"2fa_method": "backup_code" if is_backup else "totp"}
        )

        user_row = self.db.fetch_one(
            """SELECT id, account_type, username, email, permissions, created_at,
                      updated_at, email_verified, account_locked, locked_until,
                      failed_login_attempts, last_login_at, totp_enabled
               FROM auth_users WHERE id = ?""",
            (user_id,)
        )
        user = self._row_to_user(user_row)

        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user,
            session=session
        )

    def _create_2fa_challenge(
        self,
        user_id: int,
        device_id: Optional[int],
        ip_address: Optional[str],
        user_agent: Optional[str]
    ) -> TwoFactorChallenge:
        """Create a 2FA challenge for login."""
        challenge_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + 300  # 5 minutes

        full_token, token_hash = create_2fa_challenge_token(challenge_id)

        self.db.execute(
            """INSERT INTO auth_2fa_challenges
               (id, user_id, token_hash, device_id, ip_address, user_agent,
                created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (challenge_id, user_id, token_hash, device_id, ip_address,
             user_agent, now, expires_at)
        )

        return TwoFactorChallenge(
            id=challenge_id,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            device_id=device_id,
            ip_address=ip_address,
            user_agent=user_agent,
            token=full_token,
            token_hash=token_hash
        )

    # === Session Management ===

    def _create_session(
        self,
        user_id: int,
        device_id: Optional[int],
        ip_address: Optional[str],
        user_agent: Optional[str]
    ) -> Session:
        """Create a new session."""
        # Check session limit
        max_sessions = self._get_config("sessions.max_per_user", 10)
        active_count = self.db.fetch_one(
            """SELECT COUNT(*) as count FROM auth_sessions 
               WHERE user_id = ? AND revoked = 0 AND expires_at > ?""",
            (user_id, self._current_time())
        )

        if active_count and active_count["count"] >= max_sessions:
            # Revoke oldest session
            oldest = self.db.fetch_one(
                """SELECT id FROM auth_sessions 
                   WHERE user_id = ? AND revoked = 0
                   ORDER BY created_at ASC LIMIT 1""",
                (user_id,)
            )
            if oldest:
                self.db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
                    (oldest["id"],)
                )
                logger.info(f"Revoked oldest session due to limit: {oldest['id']}")

        session_id = generate_snowflake_id()
        now = self._current_time()
        expire_hours = self._get_config("sessions.expire_hours", 168)
        expires_at = now + (expire_hours * 3600)

        token_bytes = self._get_config("sessions.token_bytes", 32)
        full_token, token_hash = create_session_token(session_id, token_bytes)

        self.db.execute(
            """INSERT INTO auth_sessions
               (id, user_id, token_hash, device_id, ip_address, user_agent,
                created_at, expires_at, last_activity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, token_hash, device_id, ip_address,
             user_agent, now, expires_at, now)
        )

        logger.debug(f"Session created: {session_id} for user: {user_id}")

        return Session(
            id=session_id,
            user_id=user_id,
            device_id=device_id,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=now,
            expires_at=expires_at,
            last_activity=now,
            token=full_token,
            token_hash=token_hash
        )

    def verify_token(self, token: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> TokenInfo:
        """
        Verify a session or bot token.
        
        Features:
        - Redis caching for validated tokens (30s TTL) - skips DB on cache hit
        - Rate limiting on verification attempts per IP
        - Graceful fallback to DB-only when Redis unavailable
        """
        parsed = parse_token(token)
        if not parsed:
            raise TokenInvalidError("Invalid token format")

        # Rate limit token verification attempts per IP to prevent brute force
        if ip_address:
            rate_limit_key = f"token_verify:{ip_address}"
            max_attempts = self._get_config("security.token_verify_rate_limit", 100)
            allowed, remaining = check_rate_limit(rate_limit_key, max_attempts, 60)
            if not allowed:
                logger.warning(f"Token verification rate limit exceeded for IP: {ip_address}")
                raise TokenInvalidError("Too many verification attempts")

        # Generate cache key from token hash (not the token itself for security)
        cache_key = f"token:{hash_token(token)[:16]}"

        # Try cache first (only if Redis available)
        if redis_available():
            cached = cache_get(cache_key)
            if cached:
                # Validate IP binding if enabled
                if self._get_config("security.token_binding", False):
                    if ip_address and cached.get("bound_ip") and cached["bound_ip"] != ip_address:
                        logger.warning(f"Token IP mismatch: expected {cached['bound_ip']}, got {ip_address}")
                        cache_delete(cache_key)
                        raise TokenInvalidError("Token bound to different IP")
                    
                    if user_agent and cached.get("bound_ua") and cached["bound_ua"] != user_agent:
                        logger.warning(f"Token UA mismatch")
                        cache_delete(cache_key)
                        raise TokenInvalidError("Token bound to different browser")

                logger.debug(f"Token cache hit for {parsed['token_type']}")
                return TokenInfo(
                    valid=cached["valid"],
                    token_type=cached["token_type"],
                    account_id=cached["account_id"],
                    user_id=cached["user_id"],
                    session_id=cached.get("session_id"),
                    permissions=cached["permissions"],
                    rate_limit_tier=cached["rate_limit_tier"],
                    expires_at=cached.get("expires_at"),
                    username=cached["username"],
                    account_type=AccountType(cached["account_type"]),
                    avatar_url=cached.get("avatar_url")
                )

        # Cache miss or Redis unavailable - verify from DB
        if parsed["token_type"] == "session":
            token_info = self._verify_session_token(parsed, ip_address, user_agent)
        elif parsed["token_type"] == "bot":
            token_info = self._verify_bot_token(parsed, ip_address)
        else:
            raise TokenInvalidError("Invalid token type")

        # Cache the result (30s TTL - short enough for quick revocation)
        if redis_available():
            cache_ttl = self._get_config("security.token_cache_ttl", 30)
            cache_data = {
                "valid": token_info.valid,
                "token_type": token_info.token_type,
                "account_id": token_info.account_id,
                "user_id": token_info.user_id,
                "session_id": token_info.session_id,
                "permissions": token_info.permissions,
                "rate_limit_tier": token_info.rate_limit_tier,
                "expires_at": token_info.expires_at,
                "username": token_info.username,
                "account_type": token_info.account_type.value,
                "avatar_url": token_info.avatar_url,
            }
            # Add binding if enabled
            if self._get_config("security.token_binding", False):
                if ip_address:
                    cache_data["bound_ip"] = ip_address
                if user_agent:
                    cache_data["bound_ua"] = user_agent

            cache_set(cache_key, cache_data, ttl=cache_ttl)

        return token_info

    def _verify_session_token(
        self,
        parsed: dict,
        ip_address: Optional[str],
        user_agent: Optional[str] = None
    ) -> TokenInfo:
        """Verify a user session token."""
        session = self.db.fetch_one(
            """SELECT s.id, s.user_id, s.token_hash, s.expires_at, s.revoked,
                      s.last_activity, s.ip_address, s.user_agent,
                      u.username, u.permissions, u.account_type, u.avatar_url
               FROM auth_sessions s
               JOIN auth_users u ON s.user_id = u.id
               WHERE s.id = ?""",
            (parsed["id"],)
        )

        if not session:
            raise TokenInvalidError("Session not found")

        if session["revoked"]:
            raise TokenInvalidError("Session has been revoked")

        now = self._current_time()
        if session["expires_at"] < now:
            raise TokenExpiredError("Session has expired")

        if not verify_token_hash(parsed["secret"], session["token_hash"]):
            raise TokenInvalidError("Invalid session token")

        # Validate token binding if enabled
        if self._get_config("security.token_binding", False):
            # IP Binding
            if ip_address and session["ip_address"] and session["ip_address"] != ip_address:
                logger.warning(f"Session IP mismatch for {session['id']}: expected {session['ip_address']}, got {ip_address}")
                raise TokenInvalidError("Session bound to different IP")
            
            # User-Agent Binding (more sensitive, might cause issues with some browsers/updates)
            if user_agent and session["user_agent"] and session["user_agent"] != user_agent:
                # We check if it's a major mismatch
                logger.warning(f"Session User-Agent mismatch for {session['id']}")
                raise TokenInvalidError("Session bound to different browser")

        # Update last activity
        extend_on_activity = self._get_config("sessions.extend_on_activity", True)
        threshold_hours = self._get_config("sessions.extend_threshold_hours", 24)

        if extend_on_activity:
            time_since_activity = now - session["last_activity"]
            if time_since_activity > (threshold_hours * 3600):
                expire_hours = self._get_config("sessions.expire_hours", 168)
                new_expires = now + (expire_hours * 3600)
                self.db.execute(
                    "UPDATE auth_sessions SET last_activity = ?, expires_at = ? WHERE id = ?",
                    (now, new_expires, parsed["id"])
                )
            else:
                self.db.execute(
                    "UPDATE auth_sessions SET last_activity = ? WHERE id = ?",
                    (now, parsed["id"])
                )

        # Track IP if provided
        if ip_address:
            self._track_ip(session["user_id"], ip_address)

        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=session["user_id"],
            user_id=session["user_id"],
            session_id=session["id"],
            permissions=permissions_from_json(session["permissions"]),
            rate_limit_tier="user",
            expires_at=session["expires_at"],
            username=session["username"],
            account_type=AccountType(session["account_type"]),
            avatar_url=session["avatar_url"]
        )

    def _verify_bot_token(self, parsed: dict, ip_address: Optional[str]) -> TokenInfo:
        """Verify a bot token."""
        bot = self.db.fetch_one(
            """SELECT b.id, b.owner_id, b.username, b.token_hash, b.permissions,
                      b.disabled, u.username as owner_username, u.avatar_url as owner_avatar_url
               FROM auth_bots b
               JOIN auth_users u ON b.owner_id = u.id
               WHERE b.id = ?""",
            (parsed["id"],)
        )

        if not bot:
            raise TokenInvalidError("Bot not found")

        if bot["disabled"]:
            raise TokenInvalidError("Bot is disabled")

        if not verify_token_hash(parsed["secret"], bot["token_hash"]):
            raise TokenInvalidError("Invalid bot token")

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=bot["id"],
            user_id=bot["owner_id"],
            session_id=None,
            permissions=permissions_from_json(bot["permissions"]),
            rate_limit_tier="bot",
            expires_at=None,
            username=bot["username"],
            account_type=AccountType.BOT,
            avatar_url=bot["owner_avatar_url"]
        )

    def refresh_session(self, token: str) -> Optional[str]:
        """Refresh a session token."""
        try:
            token_info = self.verify_token(token)
            if token_info.token_type != "user":
                return None

            # Get session
            session = self.db.fetch_one(
                "SELECT device_id, ip_address, user_agent FROM auth_sessions WHERE id = ?",
                (token_info.session_id,)
            )

            if not session:
                return None

            # Revoke old session
            self.db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
                (token_info.session_id,)
            )

            # Create new session
            new_session = self._create_session(
                token_info.user_id,
                session["device_id"],
                session["ip_address"],
                session["user_agent"]
            )

            logger.info(f"Session refreshed for user: {token_info.user_id}")
            return new_session.token

        except (TokenInvalidError, TokenExpiredError):
            return None

    def logout(self, token: str) -> bool:
        """Logout and invalidate a session."""
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "session":
            return False

        session = self.db.fetch_one(
            "SELECT user_id, token_hash FROM auth_sessions WHERE id = ?",
            (parsed["id"],)
        )

        if not session:
            return False

        if not verify_token_hash(parsed["secret"], session["token_hash"]):
            return False

        self.db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
            (parsed["id"],)
        )

        # Invalidate token cache
        cache_key = f"token:{hash_token(token)[:16]}"
        cache_delete(cache_key)

        logger.info(f"Session logged out: {parsed['id']}")
        self._log_audit(AuditEventType.LOGOUT, session["user_id"], True)

        return True

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
        """Logout all sessions for a user."""
        except_session_id = None
        if except_token:
            parsed = parse_token(except_token)
            if parsed and parsed["token_type"] == "session":
                except_session_id = parsed["id"]

        if except_session_id:
            result = self.db.execute(
                """UPDATE auth_sessions SET revoked = 1 
                   WHERE user_id = ? AND revoked = 0 AND id != ?""",
                (user_id, except_session_id)
            )
        else:
            result = self.db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,)
            )

        count = result.rowcount if hasattr(result, 'rowcount') else 0
        logger.info(f"Logged out {count} sessions for user: {user_id}")
        self._log_audit(
            AuditEventType.LOGOUT_ALL, user_id, True,
            details={"count": count, "except_current": except_session_id is not None}
        )

        return count

    def get_sessions(self, user_id: int) -> List[Session]:
        """Get all active sessions for a user."""
        rows = self.db.fetch_all(
            """SELECT id, user_id, device_id, ip_address, user_agent,
                      created_at, expires_at, last_activity
               FROM auth_sessions
               WHERE user_id = ? AND revoked = 0 AND expires_at > ?
               ORDER BY last_activity DESC""",
            (user_id, self._current_time())
        )

        return [
            Session(
                id=row["id"],
                user_id=row["user_id"],
                device_id=row["device_id"],
                ip_address=row["ip_address"],
                user_agent=row["user_agent"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                last_activity=row["last_activity"]
            )
            for row in rows
        ]

    def revoke_session(self, user_id: int, session_id: int) -> bool:
        """Revoke a specific session."""
        session = self.db.fetch_one(
            "SELECT user_id FROM auth_sessions WHERE id = ?",
            (session_id,)
        )

        if not session or session["user_id"] != user_id:
            return False

        self.db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
            (session_id,)
        )

        logger.info(f"Session revoked: {session_id}")
        self._log_audit(
            AuditEventType.SESSION_REVOKED, user_id, True,
            details={"session_id": session_id}
        )

        return True

    # === Two-Factor Authentication ===

    def setup_2fa(self, user_id: int) -> TwoFactorSetup:
        """Begin 2FA setup."""
        user = self.db.fetch_one(
            "SELECT username, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        if user["totp_enabled"]:
            raise AuthError("2FA is already enabled")

        # Generate secret and backup codes
        secret = totp_module.generate_totp_secret()
        backup_codes = totp_module.generate_backup_codes()

        # Store encrypted secret temporarily (not enabled yet)
        encrypted_secret = totp_module.encrypt_totp_secret(secret)
        backup_hashes = totp_module.hash_backup_codes(backup_codes)

        self.db.execute(
            """UPDATE auth_users SET totp_secret_encrypted = ?, backup_codes_hash = ?,
               updated_at = ? WHERE id = ?""",
            (encrypted_secret, json.dumps(backup_hashes), self._current_time(), user_id)
        )

        # Generate QR URI
        issuer = self._get_config("totp.issuer", "PlexiChat")
        qr_uri = totp_module.generate_totp_uri(secret, user["username"], issuer)

        logger.info(f"2FA setup initiated for user: {user_id}")

        return TwoFactorSetup(
            secret=secret,
            qr_uri=qr_uri,
            backup_codes=backup_codes,
            issuer=issuer,
            username=user["username"]
        )

    def confirm_2fa(self, user_id: int, code: str) -> bool:
        """Confirm 2FA setup with a valid code."""
        user = self.db.fetch_one(
            "SELECT totp_secret_encrypted, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        if user["totp_enabled"]:
            raise AuthError("2FA is already enabled")

        if not user["totp_secret_encrypted"]:
            raise AuthError("2FA setup not initiated")

        # Verify code
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        if not totp_module.verify_totp_code(secret, code):
            raise TwoFactorInvalidError("Invalid 2FA code")

        # Enable 2FA
        self.db.execute(
            "UPDATE auth_users SET totp_enabled = 1, updated_at = ? WHERE id = ?",
            (self._current_time(), user_id)
        )

        logger.info(f"2FA enabled for user: {user_id}")
        self._log_audit(AuditEventType.TWO_FACTOR_ENABLED, user_id, True)

        return True

    def disable_2fa(self, user_id: int, password: str, code: str) -> bool:
        """Disable 2FA."""
        user = self.db.fetch_one(
            """SELECT password_hash, totp_secret_encrypted, totp_enabled
               FROM auth_users WHERE id = ?""",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        if not user["totp_enabled"]:
            raise AuthError("2FA is not enabled")

        # Verify password
        if not verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")

        # Verify 2FA code
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        if not totp_module.verify_totp_code(secret, code):
            raise TwoFactorInvalidError("Invalid 2FA code")

        # Disable 2FA
        self.db.execute(
            """UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL,
               backup_codes_hash = NULL, updated_at = ? WHERE id = ?""",
            (self._current_time(), user_id)
        )

        logger.info(f"2FA disabled for user: {user_id}")
        self._log_audit(AuditEventType.TWO_FACTOR_DISABLED, user_id, True)

        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        """Regenerate backup codes."""
        user = self.db.fetch_one(
            "SELECT password_hash, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        if not user["totp_enabled"]:
            raise AuthError("2FA is not enabled")

        # Verify password
        if not verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")

        # Generate new codes
        backup_codes = totp_module.generate_backup_codes()
        backup_hashes = totp_module.hash_backup_codes(backup_codes)

        self.db.execute(
            "UPDATE auth_users SET backup_codes_hash = ?, updated_at = ? WHERE id = ?",
            (json.dumps(backup_hashes), self._current_time(), user_id)
        )

        logger.info(f"Backup codes regenerated for user: {user_id}")
        self._log_audit(AuditEventType.TWO_FACTOR_BACKUP_REGENERATED, user_id, True)

        return backup_codes

    def get_2fa_status(self, user_id: int) -> TwoFactorStatus:
        """Get 2FA status for a user."""
        user = self.db.fetch_one(
            "SELECT totp_enabled, backup_codes_hash FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        backup_count = 0
        if user["backup_codes_hash"]:
            backup_hashes = json.loads(user["backup_codes_hash"])
            backup_count = len(backup_hashes)

        return TwoFactorStatus(
            enabled=bool(user["totp_enabled"]),
            backup_codes_remaining=backup_count
        )

    # === Password Management ===

    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        """Change user password."""
        user = self.db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?",
            (user_id,)
        )

        if not user:
            raise UserNotFoundError("User not found")

        # Verify old password
        if not verify_password(old_password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid current password")

        # Validate new password
        validation = validate_pwd(new_password)
        if not validation.valid:
            raise WeakPasswordError(
                f"New password does not meet requirements: {', '.join(validation.issues)}",
                validation.issues
            )

        # Update password
        new_hash = hash_password(new_password)
        self.db.execute(
            "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (new_hash, self._current_time(), user_id)
        )

        logger.info(f"Password changed for user: {user_id}")
        self._log_audit(AuditEventType.PASSWORD_CHANGE, user_id, True)

        return True

    def request_password_reset(self, email: str) -> bool:
        """Request password reset email."""
        if not self.email_sender:
            logger.warning("Password reset requested but email not configured")
            return False

        user = self.db.fetch_one(
            "SELECT id FROM auth_users WHERE email = ?",
            (email,)
        )

        if not user:
            # Don't reveal if email exists
            return True

        # Create reset token
        token_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + 3600  # 1 hour

        full_token, token_hash = create_email_token(token_id)

        self.db.execute(
            """INSERT INTO auth_email_tokens
               (id, user_id, token_hash, token_type, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token_id, user["id"], token_hash, "reset_password", now, expires_at)
        )

        # Send email
        subject = "Password Reset Request"
        body = f"Click here to reset your password: {full_token}"

        try:
            self.email_sender.send(email, subject, body)
            logger.info(f"Password reset email sent to: {email}")
            self._log_audit(
                AuditEventType.PASSWORD_RESET_REQUEST, user["id"], True,
                details={"email": email}
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send password reset email: {e}")
            return False

    def reset_password(self, token: str, new_password: str) -> bool:
        """Reset password with token."""
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            raise TokenInvalidError("Invalid reset token")

        token_record = self.db.fetch_one(
            """SELECT id, user_id, token_hash, expires_at, used, token_type
               FROM auth_email_tokens WHERE id = ?""",
            (parsed["id"],)
        )

        if not token_record:
            raise TokenInvalidError("Token not found")

        if token_record["token_type"] != "reset_password":
            raise TokenInvalidError("Invalid token type")

        if token_record["used"]:
            raise TokenInvalidError("Token already used")

        if token_record["expires_at"] < self._current_time():
            raise TokenExpiredError("Token expired")

        if not verify_token_hash(parsed["secret"], token_record["token_hash"]):
            raise TokenInvalidError("Invalid token")

        # Validate new password
        validation = validate_pwd(new_password)
        if not validation.valid:
            raise WeakPasswordError(
                f"Password does not meet requirements: {', '.join(validation.issues)}",
                validation.issues
            )

        # Mark token as used
        self.db.execute(
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?",
            (parsed["id"],)
        )

        # Update password
        new_hash = hash_password(new_password)
        self.db.execute(
            "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (new_hash, self._current_time(), token_record["user_id"])
        )

        logger.info(f"Password reset for user: {token_record['user_id']}")
        self._log_audit(AuditEventType.PASSWORD_RESET, token_record["user_id"], True)

        return True

    def validate_password(self, password: str) -> PasswordValidation:
        """Validate password strength."""
        return validate_pwd(password)

    # === Bot Management ===

    def create_bot(
        self,
        owner_id: int,
        username: str,
        display_name: str,
        permissions: Optional[Dict[str, bool]] = None
    ) -> Bot:
        """Create a bot account."""
        logger.info(f"Bot creation attempt by user: {owner_id}")

        # Check owner exists and has permission
        owner = self.db.fetch_one(
            "SELECT id, totp_enabled, permissions FROM auth_users WHERE id = ?",
            (owner_id,)
        )

        if not owner:
            raise UserNotFoundError("Owner not found")

        owner_perms = permissions_from_json(owner["permissions"])
        if not has_permission(owner_perms, "bots.create"):
            raise PermissionDeniedError("You do not have permission to create bots")

        # Check if 2FA required for bot creation
        require_2fa = self._get_config("bots.require_owner_2fa", False)
        if require_2fa and not owner["totp_enabled"]:
            raise AuthError("2FA must be enabled to create bots")

        # Check bot limit
        max_bots = self._get_config("accounts.max_bots_per_user", 5)
        bot_count = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM auth_bots WHERE owner_id = ?",
            (owner_id,)
        )

        if bot_count and bot_count["count"] >= max_bots:
            raise BotLimitExceededError(f"Maximum of {max_bots} bots allowed")

        # Validate username
        valid, issues = validate_username(username)
        if not valid:
            raise InvalidUsernameError(f"Invalid bot username: {', '.join(issues)}", issues)

        # Check username not taken
        existing = self.db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?",
            (username,)
        )
        if existing:
            raise UserExistsError("Username already taken", "username")

        existing = self.db.fetch_one(
            "SELECT id FROM auth_bots WHERE username = ?",
            (username,)
        )
        if existing:
            raise UserExistsError("Username already taken by another bot", "username")

        # Set permissions
        if permissions is None:
            bot_perms = DEFAULT_BOT_PERMISSIONS.copy()
        else:
            # Validate permissions
            valid, issues = validate_permissions(permissions, is_bot=True)
            if not valid:
                raise PermissionDeniedError(f"Invalid bot permissions: {', '.join(issues)}")
            bot_perms = permissions

        # Create bot
        bot_id = generate_snowflake_id()
        now = self._current_time()

        token_bytes = self._get_config("bots.token_bytes", 48)
        full_token, token_hash = create_bot_token(bot_id, token_bytes)

        self.db.execute(
            """INSERT INTO auth_bots
               (id, owner_id, username, display_name, token_hash, permissions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (bot_id, owner_id, username, display_name, token_hash,
             permissions_to_json(bot_perms), now)
        )

        logger.info(f"Bot created: {username} (ID: {bot_id}) by user: {owner_id}")
        self._log_audit(
            AuditEventType.BOT_CREATED, owner_id, True,
            details={"bot_id": bot_id, "bot_username": username}
        )

        return Bot(
            id=bot_id,
            owner_id=owner_id,
            username=username,
            display_name=display_name,
            permissions=bot_perms,
            created_at=now,
            token=full_token
        )

    def get_bot(self, bot_id: int) -> Optional[Bot]:
        """Get a bot by ID."""
        row = self.db.fetch_one(
            """SELECT id, owner_id, username, display_name, permissions,
                      created_at, disabled
               FROM auth_bots WHERE id = ?""",
            (bot_id,)
        )

        if not row:
            return None

        return Bot(
            id=row["id"],
            owner_id=row["owner_id"],
            username=row["username"],
            display_name=row["display_name"],
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            disabled=bool(row["disabled"])
        )

    def get_user_bots(self, owner_id: int) -> List[Bot]:
        """Get all bots owned by a user."""
        rows = self.db.fetch_all(
            """SELECT id, owner_id, username, display_name, permissions,
                      created_at, disabled
               FROM auth_bots WHERE owner_id = ?
               ORDER BY created_at DESC""",
            (owner_id,)
        )

        return [
            Bot(
                id=row["id"],
                owner_id=row["owner_id"],
                username=row["username"],
                display_name=row["display_name"],
                permissions=permissions_from_json(row["permissions"]),
                created_at=row["created_at"],
                disabled=bool(row["disabled"])
            )
            for row in rows
        ]

    def regenerate_bot_token(self, owner_id: int, bot_id: int) -> str:
        """Regenerate bot token."""
        bot = self.db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        if not bot:
            raise UserNotFoundError("Bot not found")

        if bot["owner_id"] != owner_id:
            raise PermissionDeniedError("You do not own this bot")

        token_bytes = self._get_config("bots.token_bytes", 48)
        full_token, token_hash = create_bot_token(bot_id, token_bytes)

        self.db.execute(
            "UPDATE auth_bots SET token_hash = ? WHERE id = ?",
            (token_hash, bot_id)
        )

        logger.info(f"Bot token regenerated: {bot_id}")
        self._log_audit(
            AuditEventType.BOT_TOKEN_REGENERATED, owner_id, True,
            details={"bot_id": bot_id}
        )

        return full_token

    def update_bot_permissions(
        self,
        owner_id: int,
        bot_id: int,
        permissions: Dict[str, bool]
    ) -> Bot:
        """Update bot permissions."""
        bot = self.db.fetch_one(
            "SELECT owner_id, username, display_name, created_at, disabled FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        if not bot:
            raise UserNotFoundError("Bot not found")

        if bot["owner_id"] != owner_id:
            raise PermissionDeniedError("You do not own this bot")

        # Validate permissions
        valid, issues = validate_permissions(permissions, is_bot=True)
        if not valid:
            raise PermissionDeniedError(f"Invalid bot permissions: {', '.join(issues)}")

        self.db.execute(
            "UPDATE auth_bots SET permissions = ? WHERE id = ?",
            (permissions_to_json(permissions), bot_id)
        )

        logger.info(f"Bot permissions updated: {bot_id}")

        return Bot(
            id=bot_id,
            owner_id=owner_id,
            username=bot["username"],
            display_name=bot["display_name"],
            permissions=permissions,
            created_at=bot["created_at"],
            disabled=bool(bot["disabled"])
        )

    def disable_bot(self, owner_id: int, bot_id: int) -> bool:
        """Disable a bot."""
        bot = self.db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        if not bot:
            raise UserNotFoundError("Bot not found")

        if bot["owner_id"] != owner_id:
            raise PermissionDeniedError("You do not own this bot")

        self.db.execute(
            "UPDATE auth_bots SET disabled = 1 WHERE id = ?",
            (bot_id,)
        )

        logger.info(f"Bot disabled: {bot_id}")
        return True

    def enable_bot(self, owner_id: int, bot_id: int) -> bool:
        """Enable a bot."""
        bot = self.db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        if not bot:
            raise UserNotFoundError("Bot not found")

        if bot["owner_id"] != owner_id:
            raise PermissionDeniedError("You do not own this bot")

        self.db.execute(
            "UPDATE auth_bots SET disabled = 0 WHERE id = ?",
            (bot_id,)
        )

        logger.info(f"Bot enabled: {bot_id}")
        return True

    def delete_bot(self, owner_id: int, bot_id: int) -> bool:
        """Delete a bot."""
        bot = self.db.fetch_one(
            "SELECT owner_id, username FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        if not bot:
            raise UserNotFoundError("Bot not found")

        if bot["owner_id"] != owner_id:
            raise PermissionDeniedError("You do not own this bot")

        self.db.execute(
            "DELETE FROM auth_bots WHERE id = ?",
            (bot_id,)
        )

        logger.info(f"Bot deleted: {bot_id}")
        self._log_audit(
            AuditEventType.BOT_DELETED, owner_id, True,
            details={"bot_id": bot_id, "bot_username": bot["username"]}
        )

        return True

    # === Device Management ===

    def _track_device(self, user_id: int, device_info: Dict[str, str]) -> Optional[int]:
        """Track a device, creating or updating as needed."""
        fingerprint = device_info.get("fingerprint", "")
        if not fingerprint:
            return None

        existing = self.db.fetch_one(
            "SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint)
        )

        now = self._current_time()

        if existing:
            self.db.execute(
                "UPDATE auth_devices SET last_seen_at = ? WHERE id = ?",
                (now, existing["id"])
            )
            return existing["id"]
        else:
            device_id = generate_snowflake_id()
            self.db.execute(
                """INSERT INTO auth_devices
                   (id, user_id, fingerprint, name, device_type, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (device_id, user_id, fingerprint,
                 device_info.get("name"), device_info.get("type"),
                 now, now)
            )
            logger.debug(f"New device tracked for user {user_id}: {device_id}")
            return device_id

    def _track_ip(self, user_id: int, ip_address: str) -> None:
        """Track an IP address for a user."""
        if not ip_address:
            return

        existing = self.db.fetch_one(
            "SELECT id FROM auth_known_ips WHERE user_id = ? AND ip_address = ?",
            (user_id, ip_address)
        )

        now = self._current_time()

        if existing:
            self.db.execute(
                "UPDATE auth_known_ips SET last_seen_at = ? WHERE id = ?",
                (now, existing["id"])
            )
        else:
            ip_id = generate_snowflake_id()
            self.db.execute(
                """INSERT INTO auth_known_ips
                   (id, user_id, ip_address, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (ip_id, user_id, ip_address, now, now)
            )
            logger.debug(f"New IP tracked for user {user_id}: {ip_address}")

    def get_devices(self, user_id: int) -> List[Device]:
        """Get all known devices for a user."""
        rows = self.db.fetch_all(
            """SELECT id, user_id, fingerprint, name, device_type,
                      first_seen_at, last_seen_at
               FROM auth_devices WHERE user_id = ?
               ORDER BY last_seen_at DESC""",
            (user_id,)
        )

        return [
            Device(
                id=row["id"],
                user_id=row["user_id"],
                fingerprint=row["fingerprint"],
                name=row["name"],
                device_type=row["device_type"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"]
            )
            for row in rows
        ]

    def rename_device(self, user_id: int, device_id: int, name: str) -> bool:
        """Rename a device."""
        device = self.db.fetch_one(
            "SELECT user_id FROM auth_devices WHERE id = ?",
            (device_id,)
        )

        if not device or device["user_id"] != user_id:
            return False

        self.db.execute(
            "UPDATE auth_devices SET name = ? WHERE id = ?",
            (name, device_id)
        )

        return True

    def revoke_device(self, user_id: int, device_id: int) -> bool:
        """Revoke a device and all its sessions."""
        device = self.db.fetch_one(
            "SELECT user_id FROM auth_devices WHERE id = ?",
            (device_id,)
        )

        if not device or device["user_id"] != user_id:
            return False

        # Revoke all sessions for this device
        self.db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?",
            (device_id,)
        )

        # Delete device
        self.db.execute(
            "DELETE FROM auth_devices WHERE id = ?",
            (device_id,)
        )

        logger.info(f"Device revoked: {device_id}")
        self._log_audit(
            AuditEventType.DEVICE_REVOKED, user_id, True,
            details={"device_id": device_id}
        )

        return True

    # === Audit ===

    def get_login_history(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        """Get login history for a user."""
        rows = self.db.fetch_all(
            """SELECT id, user_id, event_type, ip_address, device_id,
                      timestamp, details, success
               FROM auth_audit_log
               WHERE user_id = ? AND event_type IN (?, ?, ?)
               ORDER BY timestamp DESC LIMIT ?""",
            (user_id, AuditEventType.LOGIN_SUCCESS.value,
             AuditEventType.LOGIN_FAILED.value, AuditEventType.LOGOUT.value, limit)
        )

        return [self._row_to_audit_entry(row) for row in rows]

    def get_security_events(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        """Get security events for a user."""
        rows = self.db.fetch_all(
            """SELECT id, user_id, event_type, ip_address, device_id,
                      timestamp, details, success
               FROM auth_audit_log
               WHERE user_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (user_id, limit)
        )

        return [self._row_to_audit_entry(row) for row in rows]

    def _row_to_audit_entry(self, row) -> AuditEntry:
        """Convert database row to AuditEntry."""
        details = None
        if row["details"]:
            try:
                details = json.loads(row["details"])
            except json.JSONDecodeError:
                details = None

        return AuditEntry(
            id=row["id"],
            user_id=row["user_id"],
            event_type=AuditEventType(row["event_type"]),
            ip_address=row["ip_address"],
            device_id=row["device_id"],
            timestamp=row["timestamp"],
            details=details,
            success=bool(row["success"])
        )

    # === Utility ===

    def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID (cached)."""
        # Check cache first
        cached = self._cache_get_user(user_id)
        if cached is not None:
            return cached

        row = self.db.fetch_one(
            """SELECT id, account_type, username, email, permissions, created_at,
                      updated_at, email_verified, account_locked, locked_until,
                      failed_login_attempts, last_login_at, totp_enabled
               FROM auth_users WHERE id = ?""",
            (user_id,)
        )

        if not row:
            return None

        user = self._row_to_user(row)
        self._cache_set_user(user_id, user)
        return user

    def get_user_by_username(self, username: str) -> Optional[User]:
        """Get a user by username."""
        row = self.db.fetch_one(
            """SELECT id, account_type, username, email, permissions, created_at,
                      updated_at, email_verified, account_locked, locked_until,
                      failed_login_attempts, last_login_at, totp_enabled
               FROM auth_users WHERE username = ?""",
            (username,)
        )

        if not row:
            return None

        return self._row_to_user(row)

    def get_users_bulk(self, user_ids: List[int]) -> Dict[int, User]:
        """
        Get multiple users by ID in a single query (optimized for bulk lookups).
        
        Args:
            user_ids: List of user IDs to fetch
            
        Returns:
            Dict mapping user_id to User object
        """
        if not user_ids:
            return {}

        result = {}
        uncached_ids = []

        # Check cache first
        for uid in user_ids:
            cached = self._cache_get_user(uid)
            if cached is not None:
                result[uid] = cached
            else:
                uncached_ids.append(uid)

        # Fetch uncached users in single query
        if uncached_ids:
            placeholders = ",".join("?" * len(uncached_ids))
            rows = self.db.fetch_all(
                f"""SELECT id, account_type, username, email, permissions, created_at,
                          updated_at, email_verified, account_locked, locked_until,
                          failed_login_attempts, last_login_at, totp_enabled
                   FROM auth_users WHERE id IN ({placeholders})""",
                tuple(uncached_ids)
            )

            for row in rows:
                user = self._row_to_user(row)
                result[user.id] = user
                self._cache_set_user(user.id, user)

        return result

    def _row_to_user(self, row) -> User:
        """Convert database row to User model."""
        return User(
            id=row["id"],
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=row["email"],
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            email_verified=bool(row["email_verified"]),
            account_locked=bool(row["account_locked"]),
            locked_until=row["locked_until"],
            failed_login_attempts=row["failed_login_attempts"],
            last_login_at=row["last_login_at"],
            totp_enabled=bool(row["totp_enabled"])
        )
