"""
Hardened AuthManager - Secure authentication logic.
"""

import time
import json
import secrets
import ipaddress
from typing import Optional, List, Dict, Any, Tuple

import utils.config as config
import utils.logger as logger

from src.core.base import BaseManager, SnowflakeID
from src.utils.encryption import EncryptionManager

from src.core.database import cached, invalidate_pattern
from .blacklist import BlacklistManager
from .models import (
    User,
    Session,
    Bot,
    AccessToken,
    Device,
    AuditEntry,
    TokenInfo,
    AuthResult,
    TwoFactorSetup,
    TwoFactorStatus,
    PasswordValidation,
    TwoFactorChallenge,
    AccountType,
    AuthStatus,
    AuditEventType,
)
from .exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    EmailNotVerifiedError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorInvalidError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
    InvalidUsernameError,
    InvalidEmailError,
    PermissionDeniedError,
    BotLimitExceededError,
)
from .permissions import (
    DEFAULT_USER_PERMISSIONS,
    DEFAULT_BOT_PERMISSIONS,
    permissions_to_json,
    permissions_from_json,
    has_permission,
    validate_permissions,
)
from .tokens import (
    create_session_token,
    create_bot_token,
    create_email_token,
    create_2fa_challenge_token,
    parse_token,
    verify_token_hash,
)
from .deletion_log import DeletionLog
from .passwords import (
    validate_password as validate_pwd,
    validate_username,
    validate_email,
)
from . import totp as totp_module


class AuthManager(BaseManager):
    # Exposed for convenience and test compatibility
    Status = AuthStatus
    AuthStatus = AuthStatus
    AuditEventType = AuditEventType
    AccountType = AccountType

    # Exceptions
    AuthError = AuthError
    InvalidCredentialsError = InvalidCredentialsError
    AccountLockedError = AccountLockedError
    TokenExpiredError = TokenExpiredError
    TokenInvalidError = TokenInvalidError
    TwoFactorInvalidError = TwoFactorInvalidError
    UserExistsError = UserExistsError
    UserNotFoundError = UserNotFoundError
    WeakPasswordError = WeakPasswordError
    InvalidUsernameError = InvalidUsernameError
    InvalidEmailError = InvalidEmailError
    PermissionDeniedError = PermissionDeniedError
    BotLimitExceededError = BotLimitExceededError

    def __init__(self, db, email_sender=None):
        super().__init__(db)
        self.email_sender = email_sender
        self.crypto = EncryptionManager()
        self._config = config.get("authentication", {})
        logger.info("Initializing authentication module")
        self.blacklist = BlacklistManager(db)
        self.deletion_log = DeletionLog()
        self._ensure_system_user()

    def _json_dumps(self, data: Any) -> str:
        """Serialize data to JSON string."""
        return json.dumps(data)

    def _json_loads(self, data: str) -> Any:
        """Deserialize data from JSON string."""
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    def _ensure_system_user(self) -> None:
        now = self._get_timestamp()
        self._db.insert_or_ignore(
            "auth_users",
            [
                "id",
                "account_type",
                "username",
                "email_index",
                "password_hash",
                "permissions",
                "created_at",
                "updated_at",
                "email_verified",
                "account_locked",
                "age_verified",
            ],
            (
                0,
                "system",
                "System",
                "system_index",
                "INVALID_HASH_LOCKED",
                permissions_to_json({}),
                now,
                now,
                1,
                1,
                1,
            ),
        )

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _get_config(self, key: str, default: Any = None) -> Any:
        try:
            return self._config.get(key, default)
        except Exception:
            return config.get(key, default)

    def _log_audit(
        self,
        event_type: AuditEventType,
        user_id: Optional[SnowflakeID],
        success: bool,
        ip_address: Optional[str] = None,
        device_id: Optional[SnowflakeID] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        audit_id = self._generate_id()
        details_encrypted = (
            self.crypto.encrypt_data(json.dumps(details), context=str(audit_id))
            if details
            else None
        )

        ip_index = None
        ip_encrypted = None
        if ip_address:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip_address, context=str(audit_id))

        self._db.insert_or_ignore(
            "auth_audit_log",
            [
                "id",
                "user_id",
                "event_type",
                "ip_index",
                "ip_encrypted",
                "device_id",
                "timestamp",
                "details_encrypted",
                "success",
            ],
            (
                audit_id,
                user_id,
                event_type.value,
                ip_index,
                ip_encrypted,
                device_id,
                self._get_timestamp(),
                details_encrypted,
                1 if success else 0,
            ),
        )

    # === Registration & Verification ===

    def register(
        self,
        username: str,
        email: str,
        password: str,
        device_info: Optional[Dict[str, str]] = None,
        ip_address: Optional[str] = None,
        age: Optional[int] = None,
        dob: Optional[str] = None,
    ) -> User:
        # Generate ID first to use as encryption context
        user_id = self._generate_id()

        # Configuration check for age gate
        accounts_config = self._config.get("accounts", {})
        age_gate_enabled = accounts_config.get("age_gate_enabled", False)
        min_age = accounts_config.get("minimum_age", 13)
        verification_type = accounts_config.get("age_verification_type", "boolean")

        age_verified = 0
        stored_dob = None

        if age_gate_enabled:
            if verification_type == "dob":
                if not dob:
                    raise AuthError("Date of birth is required", "dob")
                try:
                    from datetime import datetime

                    birth_date = datetime.strptime(dob, "%Y-%m-%d")
                    today = datetime.today()
                    calc_age = (
                        today.year
                        - birth_date.year
                        - (
                            (today.month, today.day)
                            < (birth_date.month, birth_date.day)
                        )
                    )
                    if calc_age < min_age:
                        raise AuthError(
                            f"Minimum age requirement not met ({min_age})", "age"
                        )
                    age_verified = 1
                    # Encrypt DOB for storage using user_id as context
                    stored_dob = self.crypto.encrypt_data(dob, context=str(user_id))
                except ValueError:
                    raise AuthError("Invalid date format. Use YYYY-MM-DD", "dob")
            else:
                # Boolean/Age mode
                if age is not None:
                    if age < min_age:
                        raise AuthError(
                            f"Minimum age requirement not met ({min_age})", "age"
                        )
                    age_verified = 1
                else:
                    # If neither age nor dob is provided but gate is enabled
                    raise AuthError("Age is required", "age")

        valid, issues = validate_username(username)
        if not valid:
            raise InvalidUsernameError(f"Invalid: {issues}", issues)

        # Check blacklist
        blocked, reason = self.blacklist.is_blocked(username)
        if blocked:
            raise InvalidUsernameError(
                f"Username is blocked: {reason}", [reason or "Blocked"]
            )

        if not validate_email(email):
            raise InvalidEmailError("Invalid email")

        pwd_val = validate_pwd(password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)

        if self._db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        ):
            # Generic error to prevent enumeration
            raise UserExistsError("Registration failed", "username")

        email_index = self.crypto.blind_index(email, "user_email")
        if self._db.fetch_one(
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        ):
            # Generic error to prevent enumeration
            raise UserExistsError("Registration failed", "email")

        now = self._get_timestamp()
        email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))
        password_hash = self.crypto.hash_password(password)
        require_ver = accounts_config.get("require_email_verification", False)

        self._db.execute(
            "INSERT INTO auth_users (id, account_type, username, email_index, email_encrypted, password_hash, permissions, created_at, updated_at, email_verified, age_verified, date_of_birth) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                AccountType.USER.value,
                username,
                email_index,
                email_encrypted,
                password_hash,
                permissions_to_json(DEFAULT_USER_PERMISSIONS),
                now,
                now,
                1 if not require_ver else 0,
                age_verified,
                stored_dob,
            ),
        )

        if ip_address:
            self._track_ip(user_id, ip_address)
        if device_info:
            self._track_device(user_id, device_info)
        self._log_audit(AuditEventType.REGISTER, user_id, True, ip_address)

        if require_ver and self.email_sender:
            self._send_verification_email(user_id, email)

        return User(
            id=user_id,
            account_type=AccountType.USER,
            username=username,
            email=email,
            permissions=DEFAULT_USER_PERMISSIONS.copy(),
            created_at=now,
            updated_at=now,
            email_verified=not require_ver,
            age_verified=bool(age_verified),
            date_of_birth=dob
            if (age_gate_enabled and verification_type == "dob")
            else None,
        )

    def verify_email(self, token: str) -> bool:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            return False
        rec = self._db.fetch_one(
            "SELECT * FROM auth_email_tokens WHERE id = ?", (parsed["id"],)
        )
        if not rec or rec["used"] or rec["expires_at"] < self._get_timestamp():
            return False
        if rec["token_type"] != "verify_email":
            return False
        if not verify_token_hash(parsed["secret"], rec["token_hash"]):
            return False

        self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?", (rec["id"],)
        )
        self._db.execute(
            "UPDATE auth_users SET email_verified = 1 WHERE id = ?", (rec["user_id"],)
        )
        self._log_audit(AuditEventType.EMAIL_VERIFIED, rec["user_id"], True)
        return True

    def resend_verification(self, email: str) -> bool:
        email_index = self.crypto.blind_index(email, "user_email")
        row = self._db.fetch_one(
            "SELECT id, email_verified FROM auth_users WHERE email_index = ?",
            (email_index,),
        )
        if not row or row["email_verified"]:
            return True
        return self._send_verification_email(row["id"], email)

    def _send_verification_email(self, user_id: int, email: str) -> bool:
        if not self.email_sender:
            return False
        tid = self._generate_id()
        token, token_hash = create_email_token(tid)
        now = self._get_timestamp()
        self._db.execute(
            "INSERT INTO auth_email_tokens (id, user_id, token_hash, token_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, user_id, token_hash, "verify_email", now, now + 86400),
        )
        try:
            self.email_sender.send(
                email, "Verify Email", f"Verification Token: {token}"
            )
            return True
        except Exception as e:
            logger.error(f"Email failed: {e}")
            return False

    # === Login & Session ===

    def login(
        self,
        username: str,
        password: str,
        device_info: Optional[Dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        email_index = self.crypto.blind_index(username, "user_email")
        row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE username = ? OR email_index = ?",
            (username, email_index),
        )

        if not row:
            self._log_audit(AuditEventType.LOGIN_FAILED, None, False, ip_address)
            raise InvalidCredentialsError("Invalid credentials")

        user_id = row["id"]
        
        # Hard Freeze: Check for deletion status
        deletion_status = row.get("deletion_status", "active")
        if deletion_status == "frozen":
            deletion_at = row.get("deletion_at")
            logger.warning(f"Login attempt for frozen account {user_id}")
            # Format time for the error message
            import datetime
            dt = datetime.datetime.fromtimestamp(deletion_at) if deletion_at else "unknown"
            raise AccountLockedError(f"Account is scheduled for deletion on {dt}. Contact an administrator to cancel.")

        if row["account_locked"]:
            # Check if lock is permanent (no expiry) or active (expiry in future)
            if not row["locked_until"] or row["locked_until"] > self._get_timestamp():
                raise AccountLockedError("Account locked", row["locked_until"])

            # Auto-unlock only if lock has expired
            self._db.execute(
                "UPDATE auth_users SET account_locked = 0, failed_login_attempts = 0 WHERE id = ?",
                (user_id,),
            )

        if not self.crypto.verify_password(password, row["password_hash"]):
            self._log_audit(AuditEventType.LOGIN_FAILED, user_id, False, ip_address)
            self._handle_failed_login(user_id, ip_address)
            raise InvalidCredentialsError("Invalid credentials")

        accounts_config = self._config.get("accounts", {})
        if accounts_config.get("require_email_verification", False) and not bool(
            row.get("email_verified", 0)
        ):
            raise EmailNotVerifiedError("Email not verified")

        device_id = self._track_device(user_id, device_info) if device_info else None
        if ip_address:
            self._track_ip(user_id, ip_address)

        if row["totp_enabled"]:
            challenge = self._create_2fa_challenge(
                user_id, device_id, ip_address, user_agent
            )
            return AuthResult(
                status=AuthStatus.TWO_FACTOR_REQUIRED,
                challenge_token=challenge.token,
                methods=["totp", "backup_code"],
                expires_in=300,
            )

        session = self._create_session(user_id, device_id, ip_address, user_agent)
        self._db.execute(
            "UPDATE auth_users SET failed_login_attempts = 0, last_login_at = ? WHERE id = ?",
            (self._get_timestamp(), user_id),
        )

        email = None
        if row["email_encrypted"]:
            try:
                email = self.crypto.decrypt_data(row["email_encrypted"], context=str(user_id))
            except Exception:
                email = "[decryption failed]"

        user = User(
            id=user_id,
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=email,
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            email_verified=bool(row.get("email_verified", 0)),
            account_locked=bool(row.get("account_locked", 0)),
            force_username_change=bool(row.get("force_username_change", 0)),
            failed_login_attempts=row.get("failed_login_attempts", 0),
            locked_until=row.get("locked_until"),
            last_login_at=row.get("last_login_at"),
            totp_enabled=bool(row.get("totp_enabled", 0)),
            age_verified=bool(row.get("age_verified", 0)),
            date_of_birth=row.get("date_of_birth"),
        )
        self._log_audit(
            AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
        )
        return AuthResult(
            status=AuthStatus.SUCCESS, token=session.token, user=user, session=session
        )

    def _handle_failed_login(self, user_id: int, ip_address: Optional[str]):
        # Increment failed login attempts
        self._db.execute(
            "UPDATE auth_users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )

        # Get threshold from config or default to 5
        security_config = self._config.get("security", {})
        threshold = security_config.get("max_failed_attempts", 5)

        # Check current attempts
        row = self._db.fetch_one(
            "SELECT failed_login_attempts FROM auth_users WHERE id = ?", (user_id,)
        )

        if row and row["failed_login_attempts"] >= threshold:
            # Default lock time 15 mins (60000ms * 15)
            lock_duration_min = security_config.get("lockout_duration_minutes", 15)
            lock_until = self._get_timestamp() + (lock_duration_min * 60 * 1000)
            self._db.execute(
                "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
                (lock_until, user_id),
            )
            self._log_audit(AuditEventType.ACCOUNT_LOCKED, user_id, True, ip_address)

        # Invalidate cache so that subsequent get_user calls see the updated failed_login_attempts
        invalidate_pattern("user_data:*")

    def _create_session(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> Session:
        sid = self._generate_id()
        now = self._get_timestamp()
        # Get session lifetime from config (default 30 days)
        sessions_config = self._config.get("sessions", {})
        expire_hours = sessions_config.get("expire_hours", 720)
        expires = now + (expire_hours * 3600 * 1000)
        token, token_hash = create_session_token(sid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(sid))

        # Enforce session limit
        max_sessions = sessions_config.get("max_per_user", 10)
        sessions = self.get_sessions(user_id)
        if len(sessions) >= max_sessions:
            # Sort by last activity to find oldest
            sessions.sort(key=lambda x: x.last_activity)
            # Revoke until we are under limit (usually just 1)
            to_revoke = len(sessions) - max_sessions + 1
            for i in range(to_revoke):
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
                    (sessions[i].id,),
                )

        self._db.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, device_id, ip_index, ip_encrypted, user_agent, created_at, expires_at, last_activity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua,
                now,
                expires,
                now,
            ),
        )
        return Session(
            id=sid,
            user_id=user_id,
            device_id=device_id,
            ip_address=ip,
            user_agent=ua,
            created_at=now,
            expires_at=expires,
            last_activity=now,
            token=token,
        )

    @cached(
        ttl=30,
        prefix="token_verify",
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: bool(
            self._config.get("security", {}).get("token_binding", False)
            or self._config.get("security", {}).get("token_verify_rate_limit")
        ),
    )
    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        security_cfg = self._config.get("security", {})
        rate_limit = security_cfg.get("token_verify_rate_limit")
        if rate_limit and ip_address:
            from src.core.database.cache import check_rate_limit

            allowed, _ = check_rate_limit(
                key=f"token_verify:{ip_address}",
                limit=int(rate_limit),
                window_seconds=60,
            )
            if not allowed:
                raise TokenInvalidError("Token verification rate limit exceeded")

        parsed = parse_token(token)
        if not parsed:
            raise TokenInvalidError("Invalid token")
        if parsed["token_type"] == "session":
            return self._verify_session_token(parsed, ip_address, user_agent=user_agent)
        elif parsed["token_type"] == "bot":
            return self._verify_bot_token(parsed, ip_address)
        raise TokenInvalidError("Unknown type")

    def _verify_session_token(
        self, parsed: Dict, ip_address: Optional[str], user_agent: Optional[str] = None
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, u.force_username_change, u.deletion_status FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
            (parsed["id"],),
        )
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid/Revoked")
            
        # Hard Freeze check
        if row.get("deletion_status") == "frozen":
            logger.warning(f"Attempted use of token for frozen user {row['user_id']}")
            raise TokenInvalidError("Account is scheduled for deletion")
            
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            # Strict Binding: IP must be provided and must match
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")
            
            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if row.get("ip_index") and row["ip_index"] != current_ip_index:
                raise TokenInvalidError("IP Binding Mismatch")

            # Strict Binding: User-Agent must be provided and must match
            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")
                
            if row.get("user_agent") and row["user_agent"] != user_agent:
                raise TokenInvalidError("User-Agent Binding Mismatch")

        now = self._get_timestamp()

        # Only update database if last activity was more than 60 seconds ago
        # to reduce write pressure on frequent polling/requests
        last_activity = row.get("last_activity", 0)
        should_update = (now - last_activity) > 60000
        sessions_config = self._config.get("sessions", {})
        if sessions_config.get("extend_on_activity", True):
            extend_threshold_hours = sessions_config.get("extend_threshold_hours", 24)
            extend_threshold = extend_threshold_hours * 3600 * 1000
            if extend_threshold_hours == 0 or (row["expires_at"] - now) <= extend_threshold:
                should_update = True

        if should_update:
            updates = ["last_activity = ?"]
            params = [now]

            # Extend session if enabled
            if sessions_config.get("extend_on_activity", True):
                extend_threshold_hours = sessions_config.get("extend_threshold_hours", 24)
                extend_threshold = extend_threshold_hours * 3600 * 1000
                if extend_threshold_hours == 0 or (row["expires_at"] - now) <= extend_threshold:
                    expire_hours = sessions_config.get("expire_hours", 720)
                    new_expires = now + (expire_hours * 3600 * 1000)
                    updates.append("expires_at = ?")
                    params.append(new_expires)

            params.append(row["id"])
            self._db.execute(
                f"UPDATE auth_sessions SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

        # Get rate limit tier (simplified for now)
        rate_limit_tier = row.get("rate_limit_tier", "standard")

        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=row["user_id"],
            user_id=row["user_id"],
            session_id=row["id"],
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType(row["account_type"]),
            rate_limit_tier=rate_limit_tier,
            expires_at=row["expires_at"],
            account_locked=bool(row.get("account_locked", 0)),
            force_username_change=bool(row.get("force_username_change", 0)),
            deletion_status=row.get("deletion_status", "active"),
        )

    def _verify_bot_token(self, parsed: Dict, ip_address: Optional[str]) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT * FROM auth_bots WHERE id = ?", (parsed["id"],)
        )
        if (
            not row
            or row["disabled"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid Bot")

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=row["id"],
            user_id=row["owner_id"],
            session_id=None,
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType.BOT,
            rate_limit_tier="bot",
            expires_at=None,
        )

    def refresh_session(self, token: str) -> Optional[str]:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "session":
            return None
        row = self._db.fetch_one(
            "SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],)
        )
        if not row or row["revoked"]:
            return None
        if row["expires_at"] < self._get_timestamp():
            return None
        # Actually extend the session expiry
        now = self._get_timestamp()
        expire_hours = self._config.get("sessions", {}).get("expire_hours", 720)
        new_expires = now + (expire_hours * 3600 * 1000)
        self._db.execute(
            "UPDATE auth_sessions SET expires_at = ?, last_activity = ? WHERE id = ?",
            (new_expires, now, parsed["id"]),
        )
        # Invalidate token cache so the new expiry is picked up
        invalidate_pattern("token_verify:*")
        return token

    def logout(self, token: str) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            # Get user_id before revoking for audit
            row = self._db.fetch_one(
                "SELECT user_id FROM auth_sessions WHERE id = ?", (parsed["id"],)
            )
            if row:
                # Clear token verification cache for immediate effect
                invalidate_pattern("token_verify:*")
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
                )
                self._log_audit(AuditEventType.LOGOUT, row["user_id"], True)
                return True
        return False

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
        # Clear token verification cache for immediate effect
        invalidate_pattern("token_verify:*")

        if except_token:
            parsed = parse_token(except_token)
            if parsed and parsed["token_type"] == "session":
                count_row = self._db.fetch_one(
                    "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND id != ? AND revoked = 0",
                    (user_id, parsed["id"]),
                )
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND id != ?",
                    (user_id, parsed["id"]),
                )
                return count_row["cnt"] if count_row else 0
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        return count_row["cnt"] if count_row else 0

    def logout_all_users(self) -> int:
        """Invalidate ALL active sessions for ALL users."""
        invalidate_pattern("token_verify:*")
        self._db.execute("UPDATE auth_sessions SET revoked = 1 WHERE revoked = 0")
        logger.info("Site-wide session purge: all users logged out")
        return 1

    def get_sessions(self, user_id: int) -> List[Session]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_sessions WHERE user_id = ? AND revoked = 0", (user_id,)
        )
        sessions = []
        for r in rows:
            ip_address = None
            if r.get("ip_encrypted"):
                try:
                    ip_address = self.crypto.decrypt_data(
                        r["ip_encrypted"], context=str(r["id"])
                    )
                except Exception:
                    ip_address = "[decryption failed]"
            
            sessions.append(Session(
                id=r["id"],
                user_id=r["user_id"],
                device_id=r["device_id"],
                ip_address=ip_address,
                user_agent=r["user_agent"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
                last_activity=r["last_activity"],
                revoked=bool(r["revoked"]),
            ))
        return sessions

    def revoke_session(self, user_id: int, session_id: int) -> bool:
        invalidate_pattern("token_verify:*")
        cursor = self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        if cursor.rowcount > 0:
            self._log_audit(AuditEventType.SESSION_REVOKED, user_id, True)
            return True
        return False

    # === User Profile ===

    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> User:
        updates = []
        params = []
        if username:
            current_user = self.get_user(user_id)
            if not current_user:
                raise UserNotFoundError("User not found")

            valid, issues = validate_username(username)
            if not valid:
                raise InvalidUsernameError(f"Invalid: {issues}", issues)

            old_username = (
                current_user.username
                if getattr(current_user, "force_username_change", False)
                else None
            )
            blocked, reason = self.blacklist.is_blocked(username, old_username=old_username)
            if blocked:
                raise InvalidUsernameError(
                    f"Username is blocked: {reason}", [reason or "Blocked"]
                )

            updates.append("username = ?")
            params.append(username)

            # Reset forced change flag
            updates.append("force_username_change = ?")
            params.append(0)

        if email:
            email_index = self.crypto.blind_index(email, "user_email")
            email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))
            updates.append("email_index = ?, email_encrypted = ?")
            params.extend([email_index, email_encrypted])
        if permissions is not None:
            updates.append("permissions = ?")
            params.append(permissions_to_json(permissions))

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(user_id)
            self._db.execute(
                f"UPDATE auth_users SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            # Clear auth cache so middleware sees change immediately
            invalidate_pattern("token_verify:*")
            # Clear user data cache so subsequent calls get fresh data
            invalidate_pattern("user_data:*")
            # Clear granular profile cache
            from src.core.database import cache_delete

            cache_delete(f"user_profile:{user_id}")

        user = self.get_user(user_id)
        if not user:
            raise UserNotFoundError("User not found")
        return user

    # === Two-Factor Authentication ===

    def _create_2fa_challenge(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> TwoFactorChallenge:
        cid = self._generate_id()
        now = self._get_timestamp()
        expires = now + 300000
        token, token_hash = create_2fa_challenge_token(cid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(cid))
        self._db.execute(
            "INSERT INTO auth_2fa_challenges (id, user_id, token_hash, device_id, ip_index, ip_encrypted, user_agent, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua,
                now,
                expires,
            ),
        )
        return TwoFactorChallenge(
            id=cid,
            user_id=user_id,
            created_at=now,
            expires_at=expires,
            device_id=device_id,
            ip_address=ip,
            user_agent=ua,
            token=token,
        )

    def complete_2fa(self, challenge_token: str, code: str) -> AuthResult:
        parsed = parse_token(challenge_token)
        if not parsed or parsed["token_type"] != "2fa":
            raise TokenInvalidError("Invalid challenge")
        row = self._db.fetch_one(
            "SELECT * FROM auth_2fa_challenges WHERE id = ?", (parsed["id"],)
        )
        if not row:
            raise TokenInvalidError("Expired/Used")
        if row["used"]:
            raise TokenInvalidError("Expired/Used")
        stored_hash = row.get("challenge_hash") or row.get("token_hash")
        if not stored_hash or not verify_token_hash(parsed["secret"], stored_hash):
            raise TokenInvalidError("Expired/Used")
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        user_row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE id = ?", (row["user_id"],)
        )
        if not user_row:
            raise UserNotFoundError("User not found")

        user_id = user_row["id"]
        secret = totp_module.decrypt_totp_secret(user_row["totp_secret_encrypted"])

        # Try TOTP code first (with replay prevention)
        try:
            is_totp_valid = totp_module.verify_totp_code(secret, code, user_id=user_id)
        except TypeError:
            is_totp_valid = totp_module.verify_totp_code(secret, code)

        if not is_totp_valid:
            # Try backup code
            backup_hashes = (
                json.loads(user_row["backup_codes_hash"])
                if user_row["backup_codes_hash"]
                else []
            )
            valid, index = totp_module.verify_backup_code(code, backup_hashes)
            if not valid:
                raise TwoFactorInvalidError("Invalid code")
            backup_hashes.pop(index)
            self._db.execute(
                "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
                (json.dumps(backup_hashes), user_id),
            )

        self._db.execute(
            "UPDATE auth_2fa_challenges SET used = 1 WHERE id = ?", (row["id"],)
        )

        ip_address = None
        try:
            if row.get("ip_encrypted"):
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context=str(row["id"])
                )
        except Exception:
            ip_address = None

        session = self._create_session(
            user_id, row["device_id"], ip_address, row["user_agent"]
        )
        email = (
            self.crypto.decrypt_data(user_row["email_encrypted"], context=str(user_id))
            if user_row["email_encrypted"]
            else None
        )
        user_obj = User(
            id=user_id,
            account_type=AccountType(user_row["account_type"]),
            username=user_row["username"],
            email=email,
            permissions=permissions_from_json(user_row["permissions"]),
            created_at=user_row["created_at"],
            updated_at=user_row["updated_at"],
            force_username_change=bool(user_row.get("force_username_change", 0)),
        )
        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user_obj,
            session=session,
        )

    def setup_2fa(self, user_id: int) -> TwoFactorSetup:
        user = self._db.fetch_one(
            "SELECT username, totp_enabled FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if user["totp_enabled"]:
            raise AuthError("2FA already enabled")
        secret = totp_module.generate_totp_secret()
        backup_codes = totp_module.generate_backup_codes()
        self._db.execute(
            "UPDATE auth_users SET totp_secret_encrypted = ?, backup_codes_hash = ? WHERE id = ?",
            (
                totp_module.encrypt_totp_secret(secret),
                json.dumps(totp_module.hash_backup_codes(backup_codes)),
                user_id,
            ),
        )
        return TwoFactorSetup(
            secret=secret,
            qr_uri=totp_module.generate_totp_uri(secret, user["username"], "Plexichat"),
            backup_codes=backup_codes,
            issuer="Plexichat",
            username=user["username"],
        )

    def confirm_2fa(self, user_id: int, code: str) -> bool:
        user = self._db.fetch_one(
            "SELECT totp_secret_encrypted FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not initiated")
        # Use a namespaced user_id for replay prevention so that codes used
        # during setup confirmation don't block the subsequent login challenge
        # within the same 30s window.
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        try:
            ok = totp_module.verify_totp_code(secret, code, user_id=f"setup:{user_id}")
        except TypeError:
            ok = totp_module.verify_totp_code(secret, code)
        if not ok:
            raise TwoFactorInvalidError("Invalid code")

        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?", (user_id,)
        )
        self._log_audit(AuditEventType.TWO_FACTOR_ENABLED, user_id, True)
        invalidate_pattern("user_data:*")
        return True

    def disable_2fa(self, user_id: int, password: str, code: str) -> bool:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_secret_encrypted, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled") or not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        # Use a namespaced user_id for replay prevention so disable flow
        # doesn't conflict with login flow within the same 30s window.
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        try:
            ok = totp_module.verify_totp_code(secret, code, user_id=f"disable:{user_id}")
        except TypeError:
            ok = totp_module.verify_totp_code(secret, code)
        if not ok:
            raise TwoFactorInvalidError("Invalid code")
        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_hash = NULL WHERE id = ?",
            (user_id,),
        )
        invalidate_pattern("user_data:*")
        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_enabled FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        codes = totp_module.generate_backup_codes()
        self._db.execute(
            "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
            (json.dumps(totp_module.hash_backup_codes(codes)), user_id),
        )
        return codes

    def get_2fa_status(self, user_id: int) -> TwoFactorStatus:
        row = self._db.fetch_one(
            "SELECT totp_enabled, backup_codes_hash FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")
        codes = json.loads(row["backup_codes_hash"]) if row["backup_codes_hash"] else []
        return TwoFactorStatus(
            enabled=bool(row["totp_enabled"]), backup_codes_remaining=len(codes)
        )

    # === Password Management ===

    def change_password(self, user_id: int, old: str, new: str) -> bool:
        user = self._db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not self.crypto.verify_password(old, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        pwd_val = validate_pwd(new)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        self._db.execute(
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new), user_id),
        )
        self._log_audit(AuditEventType.PASSWORD_CHANGE, user_id, True)
        invalidate_pattern("user_data:*")
        return True

    def request_password_reset(self, email: str) -> bool:
        email_index = self.crypto.blind_index(email, "user_email")
        user = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        )
        if not self.email_sender:
            return False
        if not user:
            return True
        tid = self._generate_id()
        token, token_hash = create_email_token(tid)
        self._db.execute(
            "INSERT INTO auth_email_tokens (id, user_id, token_hash, token_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid,
                user["id"],
                token_hash,
                "reset_password",
                self._get_timestamp(),
                self._get_timestamp() + 3600,
            ),
        )
        self.email_sender.send(email, "Reset Password", f"Token: {token}")
        return True

    def reset_password(self, token: str, new_password: str) -> bool:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            raise TokenInvalidError("Invalid token")
        rec = self._db.fetch_one(
            "SELECT * FROM auth_email_tokens WHERE id = ?", (parsed["id"],)
        )
        if (
            not rec
            or rec["used"]
            or rec["expires_at"] < self._get_timestamp()
            or rec["token_type"] != "reset_password"
        ):
            raise TokenInvalidError("Invalid token")
        if not verify_token_hash(parsed["secret"], rec["token_hash"]):
            raise TokenInvalidError("Invalid token")
        pwd_val = validate_pwd(new_password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        self._db.execute(
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new_password), rec["user_id"]),
        )
        self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?", (rec["id"],)
        )
        # Invalidate cache for this user
        invalidate_pattern(f"user_data:{rec['user_id']}*")
        invalidate_pattern(f"user_api:{rec['user_id']}*")
        return True

    def validate_password(self, password: str) -> PasswordValidation:
        return validate_pwd(password)

    # === Bot Management ===

    def create_bot(
        self,
        owner_id: int,
        username: str,
        display_name: str,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> Bot:
        owner_row = self._db.fetch_one(
            "SELECT permissions FROM auth_users WHERE id = ?", (owner_id,)
        )
        if not owner_row:
            raise UserNotFoundError("User not found")
        owner_perms = permissions_from_json(owner_row["permissions"])
        if not has_permission(owner_perms, "bots.create"):
            raise PermissionDeniedError("Missing required permission: bots.create")

        # Check if user/bot with this username already exists
        if self._db.fetch_one(
            "SELECT 1 FROM auth_users WHERE username = ?", (username,)
        ):
            # Generic error to prevent enumeration
            raise UserExistsError("Bot creation failed")
        if self._db.fetch_one(
            "SELECT 1 FROM auth_bots WHERE username = ?", (username,)
        ):
            # Generic error to prevent enumeration
            raise UserExistsError("Bot creation failed")

        # Enforce bot limit
        bot_config = self._config.get("accounts", {})
        max_bots = bot_config.get("max_bots_per_user", 5)
        current_bots = self.get_user_bots(owner_id)
        if len(current_bots) >= max_bots:
            raise BotLimitExceededError(f"User has reached the bot limit of {max_bots}")

        # Validate permissions
        requested_perms = (
            permissions if permissions is not None else DEFAULT_BOT_PERMISSIONS.copy()
        )
        valid, issues = validate_permissions(requested_perms, is_bot=True)
        if not valid:
            raise AuthError(f"Invalid permissions: {issues}")

        # DEBUG
        logger.debug(f"DEBUG_AUTH: create_bot requested_perms={requested_perms}")

        for permission, allowed in requested_perms.items():
            if allowed and not has_permission(owner_perms, permission):
                raise PermissionDeniedError(
                    f"Cannot grant bot permission not held by owner: {permission}"
                )

        # Bot should not have 'bots.create' permission
        if requested_perms.get("bots.create"):
            logger.debug("DEBUG_AUTH: Raising PermissionDeniedError for bots.create")
            raise PermissionDeniedError("Bots cannot have the 'bots.create' permission")

        bot_id = self._generate_id()
        token, token_hash = create_bot_token(bot_id)
        perms = requested_perms
        self._db.execute(
            "INSERT INTO auth_bots (id, owner_id, username, display_name, token_hash, permissions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                bot_id,
                owner_id,
                username,
                display_name,
                token_hash,
                permissions_to_json(perms),
                self._get_timestamp(),
            ),
        )
        return Bot(
            id=bot_id,
            owner_id=owner_id,
            username=username,
            display_name=display_name,
            permissions=perms.copy(),
            created_at=self._get_timestamp(),
            token=token,
        )

    def get_bot(self, bot_id: int) -> Optional[Bot]:
        row = self._db.fetch_one("SELECT * FROM auth_bots WHERE id = ?", (bot_id,))
        if not row:
            return None
        return Bot(
            id=row["id"],
            owner_id=row["owner_id"],
            username=row["username"],
            display_name=row["display_name"],
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            disabled=bool(row["disabled"]),
        )

    def get_user_bots(self, owner_id: int) -> List[Bot]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_bots WHERE owner_id = ?", (owner_id,)
        )
        return [
            Bot(
                id=r["id"],
                owner_id=r["owner_id"],
                username=r["username"],
                display_name=r["display_name"],
                permissions=permissions_from_json(r["permissions"]),
                created_at=r["created_at"],
                disabled=bool(r["disabled"]),
            )
            for r in rows
        ]

    def regenerate_bot_token(self, owner_id: int, bot_id: int) -> str:
        bot_row = self._db.fetch_one("SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,))
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        token, token_hash = create_bot_token(bot_id)
        cursor = self._db.execute(
            "UPDATE auth_bots SET token_hash = ? WHERE id = ? AND owner_id = ?",
            (token_hash, bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return token

    def update_bot_permissions(
        self, owner_id: int, bot_id: int, permissions: Dict[str, bool]
    ) -> Bot:
        bot_row = self._db.fetch_one("SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,))
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        valid, issues = validate_permissions(permissions, is_bot=True)
        if not valid:
            raise AuthError(f"Invalid permissions: {issues}")
        cursor = self._db.execute(
            "UPDATE auth_bots SET permissions = ? WHERE id = ? AND owner_id = ?",
            (permissions_to_json(permissions), bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        bot = self.get_bot(bot_id)
        if not bot:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return bot

    def disable_bot(self, owner_id: int, bot_id: int) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_bots SET disabled = 1 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True

    def enable_bot(self, owner_id: int, bot_id: int) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_bots SET disabled = 0 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True

    def delete_bot(self, owner_id: int, bot_id: int) -> bool:
        bot_row = self._db.fetch_one("SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,))
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        cursor = self._db.execute(
            "DELETE FROM auth_bots WHERE id = ? AND owner_id = ?", (bot_id, owner_id)
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True

    def create_api_access_token(
        self,
        name: Optional[str],
        created_by: Optional[int],
        token_value: Optional[str] = None,
        description: Optional[str] = None,
        expires_at: Optional[int] = None,
        scope_mode: str = "none",
    ) -> AccessToken:
        scope_mode = self._normalize_access_token_scope_mode(scope_mode)
        token_id = self._generate_id()
        token = token_value.strip() if token_value else None
        if not token:
            token = secrets.token_urlsafe(24)
        token_index = self.crypto.fast_blind_index(token, "api_access_token")
        existing = self._db.fetch_one(
            "SELECT id FROM auth_api_access_tokens WHERE token_index = ?",
            (token_index,),
        )
        if existing:
            raise ValueError("API access token already exists")
        token_encrypted = self.crypto.encrypt_data(token, context=str(token_id))
        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO auth_api_access_tokens
               (id, name, description, token_index, token_encrypted, created_by, created_at, expires_at, scope_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                name,
                description,
                token_index,
                token_encrypted,
                created_by,
                now,
                expires_at,
                scope_mode,
            ),
        )
        invalidate_pattern("access_token_required*")
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        access_token = self._row_to_access_token(row)
        access_token.token = token
        return access_token

    def list_api_access_tokens(self, include_revoked: bool = True) -> List[AccessToken]:
        now = self._get_timestamp()
        if include_revoked:
            rows = self._db.fetch_all(
                "SELECT * FROM auth_api_access_tokens ORDER BY created_at DESC"
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM auth_api_access_tokens
                   WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at DESC""",
                (now,),
            )
        return [self._row_to_access_token(row) for row in rows]

    def get_api_access_token(self, token_id: int) -> Optional[AccessToken]:
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        if not row:
            return None
        return self._row_to_access_token(row)

    def update_api_access_token(
        self,
        token_id: int,
        updated_by: Optional[int],
        name: Optional[str] = None,
        description: Optional[str] = None,
        expires_at: Optional[int] = None,
        clear_expiry: bool = False,
        scope_mode: Optional[str] = None,
    ) -> Optional[AccessToken]:
        row = self._db.fetch_one(
            "SELECT id FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        if not row:
            return None

        updates: List[str] = []
        params: List[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if clear_expiry:
            updates.append("expires_at = NULL")
        elif expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)
        if scope_mode is not None:
            updates.append("scope_mode = ?")
            params.append(self._normalize_access_token_scope_mode(scope_mode))
        if not updates:
            return self.get_api_access_token(token_id)

        params.append(token_id)
        self._db.execute(
            f"UPDATE auth_api_access_tokens SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        invalidate_pattern("access_token_required*")
        self._log_audit(
            AuditEventType.SECURITY_SETTINGS_UPDATED,
            None,
            True,
            details={"access_token_id": token_id, "fields": updates, "admin_id": updated_by},
        )
        return self.get_api_access_token(token_id)

    def revoke_api_access_token(self, token_id: int, revoked_by: Optional[int]) -> bool:
        now = self._get_timestamp()
        cursor = self._db.execute(
            "UPDATE auth_api_access_tokens SET revoked = 1, revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked = 0",
            (now, revoked_by, token_id),
        )
        if cursor.rowcount > 0:
            invalidate_pattern("access_token_required*")
            self._log_audit(
                AuditEventType.SECURITY_SETTINGS_UPDATED,
                None,
                True,
                details={"access_token_id": token_id, "action": "revoke", "admin_id": revoked_by},
            )
        return cursor.rowcount > 0

    def rotate_api_access_token(
        self,
        token_id: int,
        rotated_by: Optional[int],
        token_value: Optional[str] = None,
    ) -> Optional[AccessToken]:
        existing = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ? AND revoked = 0",
            (token_id,),
        )
        if not existing:
            return None

        new_token = self.create_api_access_token(
            name=existing["name"],
            created_by=rotated_by,
            token_value=token_value,
            description=existing["description"],
            expires_at=existing["expires_at"],
            scope_mode=existing["scope_mode"] or "none",
        )
        for scope in self.list_api_access_token_scopes(token_id):
            self.add_api_access_token_scope(
                new_token.id,
                scope["scope_type"],
                scope["value"],
                rotated_by,
            )
        self.revoke_api_access_token(token_id, rotated_by)
        self._log_audit(
            AuditEventType.SECURITY_SETTINGS_UPDATED,
            None,
            True,
            details={
                "action": "rotate_access_token",
                "old_access_token_id": token_id,
                "new_access_token_id": new_token.id,
                "admin_id": rotated_by,
            },
        )
        return new_token

    def add_api_access_token_scope(
        self,
        token_id: int,
        scope_type: str,
        value: str,
        created_by: Optional[int],
    ) -> Dict[str, Any]:
        normalized_type, normalized_value = self._normalize_access_token_scope(
            scope_type, value
        )
        token_exists = self._db.fetch_one(
            "SELECT id FROM auth_api_access_tokens WHERE id = ?",
            (token_id,),
        )
        if not token_exists:
            raise ValueError("Access token not found")
        scope_id = self._generate_id()
        created_at = self._get_timestamp()
        self._db.insert_or_ignore(
            "auth_api_access_token_scopes",
            ["id", "token_id", "scope_type", "value", "created_by", "created_at"],
            (
                scope_id,
                token_id,
                normalized_type,
                normalized_value,
                created_by,
                created_at,
            ),
        )
        row = self._db.fetch_one(
            """SELECT * FROM auth_api_access_token_scopes
               WHERE token_id = ? AND scope_type = ? AND value = ?""",
            (token_id, normalized_type, normalized_value),
        )
        if not row:
            raise ValueError("Failed to create token scope")
        return dict(row)

    def remove_api_access_token_scope(self, token_id: int, scope_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM auth_api_access_token_scopes WHERE id = ? AND token_id = ?",
            (scope_id, token_id),
        )
        return cursor.rowcount > 0

    def list_api_access_token_scopes(self, token_id: int) -> List[Dict[str, Any]]:
        rows = self._db.fetch_all(
            """SELECT * FROM auth_api_access_token_scopes
               WHERE token_id = ? ORDER BY created_at ASC""",
            (token_id,),
        )
        return [dict(row) for row in rows]

    def get_api_access_token_usage(
        self,
        token_id: int,
        recent_limit: int = 100,
    ) -> Dict[str, Any]:
        token = self.get_api_access_token(token_id)
        if not token:
            raise UserNotFoundError("Access token not found")

        recent_rows = self._db.fetch_all(
            """SELECT * FROM auth_api_access_token_events
               WHERE token_id = ? ORDER BY used_at DESC LIMIT ?""",
            (token_id, recent_limit),
        )
        recent_events = [self._row_to_access_token_event(row) for row in recent_rows]

        ip_rows = self._db.fetch_all(
            """SELECT ip_index, MAX(ip_encrypted) AS ip_encrypted, COUNT(*) AS request_count,
                      MAX(used_at) AS last_seen_at,
                      SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count
               FROM auth_api_access_token_events
               WHERE token_id = ? AND ip_index IS NOT NULL
               GROUP BY ip_index
               ORDER BY request_count DESC, last_seen_at DESC
               LIMIT 50""",
            (token_id,),
        )
        top_ips = []
        for row in ip_rows:
            ip_address = None
            if row.get("ip_encrypted"):
                try:
                    ip_address = self.crypto.decrypt_data(
                        row["ip_encrypted"], context="api_access_token_event"
                    )
                except Exception:
                    ip_address = "[decryption failed]"
            top_ips.append(
                {
                    "ip_address": ip_address or "UNKNOWN",
                    "request_count": int(row["request_count"] or 0),
                    "denied_count": int(row["denied_count"] or 0),
                    "last_seen_at": row["last_seen_at"],
                }
            )

        path_rows = self._db.fetch_all(
            """SELECT method, path, COUNT(*) AS request_count, MAX(used_at) AS last_seen_at
               FROM auth_api_access_token_events
               WHERE token_id = ? AND path IS NOT NULL
               GROUP BY method, path
               ORDER BY request_count DESC, last_seen_at DESC
               LIMIT 50""",
            (token_id,),
        )
        top_paths = [
            {
                "method": row["method"],
                "path": row["path"],
                "request_count": int(row["request_count"] or 0),
                "last_seen_at": row["last_seen_at"],
            }
            for row in path_rows
        ]

        summary = self._db.fetch_one(
            """SELECT COUNT(*) AS total_events,
                      COUNT(DISTINCT ip_index) AS distinct_ip_count,
                      SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count_total
               FROM auth_api_access_token_events
               WHERE token_id = ?""",
            (token_id,),
        ) or {}

        return {
            "token": token,
            "scopes": self.list_api_access_token_scopes(token_id),
            "recent_events": recent_events,
            "top_ips": top_ips,
            "top_paths": top_paths,
            "total_events": int(summary.get("total_events") or 0),
            "distinct_ip_count": int(summary.get("distinct_ip_count") or 0),
            "denied_count_total": int(summary.get("denied_count_total") or 0),
        }

    def verify_api_access_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
    ) -> bool:
        if not token:
            return False
        token_index = self.crypto.fast_blind_index(token, "api_access_token")
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE token_index = ?",
            (token_index,),
        )
        if not row or row["revoked"]:
            return False
        row = dict(row)
        now = self._get_timestamp()
        if row.get("expires_at") and int(row["expires_at"]) <= now:
            self._record_api_access_token_event(
                token_id=int(row["id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=None,
                reject_reason="expired",
            )
            return False

        scope_mode = self._normalize_access_token_scope_mode(row.get("scope_mode") or "none")
        scopes = self.list_api_access_token_scopes(int(row["id"]))
        scope_match = self._match_api_access_token_scope(ip_address, scopes)
        if scopes and scope_mode == "enforce" and not scope_match:
            self._record_api_access_token_event(
                token_id=int(row["id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=False,
                reject_reason="ip_scope_denied",
            )
            return False

        now = self._get_timestamp()
        last_used = row.get("last_used_at") or 0
        updates = [
            "last_used_at = ?",
            "last_used_user_agent = ?",
            "last_used_path = ?",
            "use_count_total = COALESCE(use_count_total, 0) + 1",
        ]
        params: List[Any] = [now, user_agent, path]
        if not row.get("first_used_at"):
            updates.append("first_used_at = ?")
            params.append(now)
        if ip_address:
            updates.append("last_used_ip_index = ?")
            updates.append("last_used_ip_encrypted = ?")
            params.extend(
                [
                    self.crypto.fast_blind_index(ip_address, "ip_address"),
                    self.crypto.encrypt_data(ip_address, context=str(row["id"])),
                ]
            )
        params.append(row["id"])
        self._db.execute(
            f"UPDATE auth_api_access_tokens SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        if now - int(last_used) > 60000:
            self._db.execute(
                "UPDATE auth_api_access_tokens SET last_used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        self._record_api_access_token_event(
            token_id=int(row["id"]),
            ip_address=ip_address,
            user_agent=user_agent,
            path=path,
            method=method,
            allowed=True,
            scope_match=scope_match,
            reject_reason=None,
        )
        return True

    @cached(ttl=30, prefix="access_token_required")
    def is_api_access_token_required(self) -> bool:
        now = self._get_timestamp()
        row = self._db.fetch_one(
            """SELECT id FROM auth_api_access_tokens
               WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
               LIMIT 1""",
            (now,),
        )
        return bool(row)

    def _normalize_access_token_scope_mode(self, scope_mode: str) -> str:
        normalized = (scope_mode or "none").strip().lower()
        if normalized not in {"none", "monitor", "enforce"}:
            raise ValueError("Invalid access token scope mode")
        return normalized

    def _normalize_access_token_scope(
        self,
        scope_type: str,
        value: str,
    ) -> Tuple[str, str]:
        normalized_type = (scope_type or "").strip().lower()
        normalized_value = (value or "").strip()
        if normalized_type not in {"ip", "cidr"}:
            raise ValueError("Invalid access token scope type")
        if not normalized_value:
            raise ValueError("Access token scope value is required")
        try:
            if normalized_type == "ip":
                normalized_value = str(ipaddress.ip_address(normalized_value))
            else:
                normalized_value = str(
                    ipaddress.ip_network(normalized_value, strict=False)
                )
        except ValueError as exc:
            raise ValueError("Invalid access token scope value") from exc
        return normalized_type, normalized_value

    def _match_api_access_token_scope(
        self,
        ip_address: Optional[str],
        scopes: List[Dict[str, Any]],
    ) -> Optional[bool]:
        if not scopes:
            return True
        if not ip_address:
            return False
        try:
            current_ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return False
        for scope in scopes:
            scope_type = (scope.get("scope_type") or "").lower()
            value = scope.get("value") or ""
            try:
                if scope_type == "ip" and current_ip == ipaddress.ip_address(value):
                    return True
                if scope_type == "cidr" and current_ip in ipaddress.ip_network(
                    value, strict=False
                ):
                    return True
            except ValueError:
                continue
        return False

    def _record_api_access_token_event(
        self,
        token_id: int,
        ip_address: Optional[str],
        user_agent: Optional[str],
        path: Optional[str],
        method: Optional[str],
        allowed: bool,
        scope_match: Optional[bool],
        reject_reason: Optional[str],
    ) -> None:
        event_id = self._generate_id()
        ip_index = None
        ip_encrypted = None
        if ip_address:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(
                ip_address, context="api_access_token_event"
            )
        self._db.execute(
            """INSERT INTO auth_api_access_token_events
               (id, token_id, used_at, ip_index, ip_encrypted, method, path, user_agent, allowed, scope_match, reject_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                token_id,
                self._get_timestamp(),
                ip_index,
                ip_encrypted,
                method,
                path,
                user_agent,
                1 if allowed else 0,
                None if scope_match is None else (1 if scope_match else 0),
                reject_reason,
            ),
        )

    def _row_to_access_token_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(row)
        ip_address = None
        if row.get("ip_encrypted"):
            try:
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context="api_access_token_event"
                )
            except Exception:
                ip_address = "[decryption failed]"
        return {
            "id": row["id"],
            "used_at": row["used_at"],
            "ip_address": ip_address,
            "method": row["method"],
            "path": row["path"],
            "user_agent": row["user_agent"],
            "allowed": bool(row["allowed"]),
            "scope_match": None
            if row.get("scope_match") is None
            else bool(row["scope_match"]),
            "reject_reason": row.get("reject_reason"),
        }

    def _row_to_access_token(self, row: Dict[str, Any]) -> AccessToken:
        row = dict(row)
        distinct_summary = self._db.fetch_one(
            """SELECT COUNT(DISTINCT ip_index) AS distinct_ip_count,
                      SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count_total
               FROM auth_api_access_token_events
               WHERE token_id = ?""",
            (row["id"],),
        ) or {}
        last_ip = None
        if row.get("last_used_ip_encrypted"):
            try:
                last_ip = self.crypto.decrypt_data(
                    row["last_used_ip_encrypted"], context=str(row["id"])
                )
            except Exception:
                last_ip = "[decryption failed]"
        return AccessToken(
            id=row["id"],
            name=row.get("name"),
            description=row.get("description"),
            created_by=row.get("created_by"),
            created_at=row["created_at"],
            first_used_at=row.get("first_used_at"),
            last_used_at=row.get("last_used_at"),
            last_used_ip_address=last_ip,
            last_used_user_agent=row.get("last_used_user_agent"),
            last_used_path=row.get("last_used_path"),
            expires_at=row.get("expires_at"),
            scope_mode=row.get("scope_mode") or "none",
            use_count_total=int(row.get("use_count_total") or 0),
            distinct_ip_count=int(distinct_summary.get("distinct_ip_count") or 0),
            denied_count_total=int(distinct_summary.get("denied_count_total") or 0),
            revoked=bool(row["revoked"]),
            revoked_at=row.get("revoked_at"),
            revoked_by=row.get("revoked_by"),
        )

    # === Device Management ===

    def _track_device(self, user_id: int, info: Dict) -> int:
        fp = info.get("fingerprint")
        if not fp:
            return 0
        row = self._db.fetch_one(
            "SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
            (user_id, fp),
        )
        now = self._get_timestamp()
        if row:
            self._db.execute(
                "UPDATE auth_devices SET last_seen_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            return row["id"]
        did = self._generate_id()
        self._db.execute(
            "INSERT INTO auth_devices (id, user_id, fingerprint, name, device_type, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, user_id, fp, info.get("name"), info.get("type"), now, now),
        )
        return did

    def get_devices(self, user_id: int) -> List[Device]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_devices WHERE user_id = ?", (user_id,)
        )
        return [
            Device(
                id=r["id"],
                user_id=r["user_id"],
                fingerprint=r["fingerprint"],
                name=r["name"],
                device_type=r["device_type"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
            )
            for r in rows
        ]

    def rename_device(self, user_id: int, device_id: int, name: str) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_devices SET name = ? WHERE id = ? AND user_id = ?",
            (name, device_id, user_id),
        )
        return cursor.rowcount > 0

    def revoke_device(self, user_id: int, device_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        if cursor.rowcount == 0:
            return False
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        return True

    # === IP Tracking ===

    def _track_ip(self, user_id: int, ip: str):
        record_id = self._generate_id()
        ip_index = self.crypto.fast_blind_index(ip, "ip_address")
        ip_encrypted = self.crypto.encrypt_data(ip, context=str(record_id))

        self._db.insert_or_ignore(
            "auth_known_ips",
            [
                "id",
                "user_id",
                "ip_index",
                "ip_encrypted",
                "first_seen_at",
                "last_seen_at",
            ],
            (
                record_id,
                user_id,
                ip_index,
                ip_encrypted,
                self._get_timestamp(),
                self._get_timestamp(),
            ),
        )

    # === IP Blacklisting ===

    def block_ip(
        self,
        ip_address: str,
        reason: Optional[str] = None,
        blocked_by: Optional[int] = None,
        duration_hours: Optional[int] = None,
    ) -> bool:
        """Block an IP address."""
        # Invalidate cache for this IP
        invalidate_pattern("ip_blocked:*")

        now = self._get_timestamp()
        expires_at = now + (duration_hours * 3600 * 1000) if duration_hours else None

        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
        ip_encrypted = self.crypto.encrypt_data(ip_address, context="ip_blacklist")

        self._db.upsert(
            "auth_ip_blacklist",
            [
                "ip_index",
                "ip_encrypted",
                "reason",
                "blocked_at",
                "blocked_by",
                "expires_at",
            ],
            (ip_index, ip_encrypted, reason, now, blocked_by, expires_at),
            conflict_columns=["ip_index"],
        )
        logger.info(f"IP blocked by {blocked_by}: {reason}")
        return True

    def unblock_ip(self, ip_address: str) -> bool:
        """Unblock an IP address."""
        # Invalidate cache for this IP
        invalidate_pattern("ip_blocked:*")

        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
        self._db.execute(
            "DELETE FROM auth_ip_blacklist WHERE ip_index = ?", (ip_index,)
        )
        logger.info("IP unblocked")
        return True

    @cached(ttl=300, prefix="ip_blocked")
    def is_ip_blocked(self, ip_address: str) -> bool:
        """Check if an IP address is blocked."""
        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
        row = self._db.fetch_one(
            "SELECT expires_at FROM auth_ip_blacklist WHERE ip_index = ?", (ip_index,)
        )
        if not row:
            return False

        expires_at = row["expires_at"]
        if expires_at and expires_at < self._get_timestamp():
            # Block expired, cleanup
            self.unblock_ip(ip_address)
            return False

        return True

    def get_blocked_ips(self) -> List[Dict[str, Any]]:
        """Get all blocked IPs."""
        rows = self._db.fetch_all(
            "SELECT * FROM auth_ip_blacklist ORDER BY blocked_at DESC"
        )
        for r in rows:
            try:
                r["ip_address"] = self.crypto.decrypt_data(
                    r["ip_encrypted"], context="ip_blacklist"
                )
            except Exception:
                r["ip_address"] = "UNKNOWN"
        return rows

    # === Audit & History ===

    def get_login_history(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_audit_log WHERE user_id = ? AND event_type IN (?, ?, ?) ORDER BY timestamp DESC LIMIT ?",
            (
                user_id,
                AuditEventType.LOGIN_SUCCESS.value,
                AuditEventType.LOGIN_FAILED.value,
                AuditEventType.LOGOUT.value,
                limit,
            ),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=self.crypto.decrypt_data(
                    r["ip_encrypted"], context=str(r["id"])
                )
                if r.get("ip_encrypted")
                else None,
                device_id=r["device_id"],
                timestamp=r["timestamp"],
                details=None,
                success=bool(r["success"]),
            )
            for r in rows
        ]

    def get_security_events(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_audit_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=self.crypto.decrypt_data(
                    r["ip_encrypted"], context=str(r["id"])
                )
                if r.get("ip_encrypted")
                else None,
                device_id=r["device_id"],
                timestamp=r["timestamp"],
                details=None,
                success=bool(r["success"]),
            )
            for r in rows
        ]

    # === Utility ===

    def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID (cached)."""
        data = self._get_user_data_cached(user_id)
        if not data:
            return None
        # Decrypt sensitive fields AFTER retrieval from cache (never store plaintext PII in cache)
        user_dict = dict(data)
        if user_dict.get("email_encrypted"):
            try:
                user_dict["email"] = self.crypto.decrypt_data(
                    user_dict["email_encrypted"], context=str(user_id)
                )
            except Exception:
                user_dict["email"] = None
        if user_dict.get("date_of_birth"):
            try:
                user_dict["dob_decrypted"] = self.crypto.decrypt_data(
                    user_dict["date_of_birth"], context=str(user_id)
                )
            except Exception:
                user_dict["dob_decrypted"] = None
        return self._dict_to_user(user_dict)

    @cached(ttl=60, prefix="user_data")
    def _get_user_data_cached(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Internal helper to fetch raw user data from DB for caching (including badges).

        SECURITY: This method must NOT decrypt sensitive fields (email, DOB) because
        the result is stored in Redis/DiskCache. Decryption happens in get_user() after
        retrieval from cache.
        """
        query = """
            SELECT u.*, f.badges
            FROM auth_users u
            LEFT JOIN user_features f ON u.id = f.user_id
            WHERE u.id = ?
        """
        row = self._db.fetch_one(query, (user_id,))
        if not row:
            return None

        user_dict = dict(row)

        # Parse badges (safe to cache — not PII)
        if user_dict.get("badges"):
            try:
                user_dict["badges_list"] = (
                    json.loads(user_dict["badges"])
                    if isinstance(user_dict["badges"], str)
                    else user_dict["badges"]
                )
            except Exception:
                user_dict["badges_list"] = []
        else:
            user_dict["badges_list"] = []

        return user_dict

    def _dict_to_user(self, row: Dict[str, Any]) -> User:
        """Convert a raw data dictionary to a User object."""
        return User(
            id=row["id"],
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=row.get("email"),
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            email_verified=bool(row.get("email_verified", 0)),
            account_locked=bool(row.get("account_locked", 0)),
            failed_login_attempts=row.get("failed_login_attempts", 0),
            locked_until=row.get("locked_until"),
            last_login_at=row.get("last_login_at"),
            totp_enabled=bool(row.get("totp_enabled", 0)),
            age_verified=bool(row.get("age_verified", 0)),
            date_of_birth=row.get("dob_decrypted") or row.get("date_of_birth"),
            force_username_change=bool(row.get("force_username_change", 0)),
            badges=row.get("badges_list", []),
            public_key=row.get("public_key"),
            deletion_status=row.get("deletion_status", "active"),
            deletion_at=row.get("deletion_at"),
        )

    @cached(ttl=300, prefix="user_by_username")
    def get_user_by_username(self, username: str) -> Optional[User]:
        row = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        )
        if not row:
            return None
        return self.get_user(row["id"])

    def get_user_profiles_bulk(self, user_ids: List[int]) -> Dict[str, Any]:
        """Get public profile information for multiple users (including badges, with granular Redis caching)."""
        if not user_ids:
            return {}

        result = {}
        missing_ids = []

        # 1. Try to fetch each user from Redis
        from src.core.database import cache_get, cache_set, redis_available

        for uid in user_ids:
            cache_key = f"user_profile:{uid}"
            cached_profile = cache_get(cache_key) if redis_available() else None

            if cached_profile:
                if isinstance(cached_profile, str):
                    try:
                        cached_profile = json.loads(cached_profile)
                    except Exception:
                        pass
                if isinstance(cached_profile, dict) and "created_at" in cached_profile:
                    result[str(uid)] = cached_profile
                else:
                    missing_ids.append(uid)
            else:
                missing_ids.append(uid)

        # 2. Fetch missing from DB
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            # JOIN with user_features to get badges
            query = f"""
                SELECT u.id, u.username, u.created_at, u.permissions, u.account_type, f.badges
                FROM auth_users u
                LEFT JOIN user_features f ON u.id = f.user_id
                WHERE u.id IN ({placeholders})
            """
            rows = self._db.fetch_all(query, tuple(missing_ids))

            for row in rows:
                user_id = row["id"]
                badges = []
                if row.get("badges"):
                    try:
                        badges = (
                            json.loads(row["badges"])
                            if isinstance(row["badges"], str)
                            else row["badges"]
                        )
                    except Exception:
                        badges = []

                profile = {
                    "id": user_id,
                    "username": row["username"],
                    "created_at": row["created_at"],
                    "permissions": self._json_loads(row["permissions"])
                    if isinstance(row["permissions"], str)
                    else row["permissions"],
                    "account_type": row["account_type"],
                    "avatar_url": f"/api/v1/avatars/users/{user_id}",
                    "badges": badges,
                }
                result[str(user_id)] = profile

                # Cache for next time
                if redis_available():
                    cache_set(f"user_profile:{user_id}", profile, ttl=300)

        return result

    def get_users_bulk(self, user_ids: List[int]) -> Dict[int, User]:
        """Get multiple users by ID (including badges, full data)."""
        if not user_ids:
            return {}

        placeholders = ",".join("?" for _ in user_ids)
        # JOIN with user_features to get badges
        query = f"""
            SELECT
                u.*,
                f.rate_limit_tier AS tier,
                f.badges,
                CASE WHEN u.account_type = 'bot' THEN 1 ELSE 0 END AS is_bot
            FROM auth_users u
            LEFT JOIN user_features f ON u.id = f.user_id
            WHERE u.id IN ({placeholders})
        """
        rows = self._db.fetch_all(query, tuple(user_ids))

        result = {}
        for row in rows:
            user_id = row["id"]
            user_dict = dict(row)

            # Parse badges
            if user_dict.get("badges"):
                try:
                    user_dict["badges_list"] = (
                        json.loads(user_dict["badges"])
                        if isinstance(user_dict["badges"], str)
                        else user_dict["badges"]
                    )
                except Exception:
                    user_dict["badges_list"] = []
            else:
                user_dict["badges_list"] = []

            # Decrypt sensitive fields for individual users
            if user_dict.get("email_encrypted"):
                try:
                    user_dict["email"] = self.crypto.decrypt_data(
                        user_dict["email_encrypted"], context=str(user_id)
                    )
                except Exception:
                    user_dict["email"] = None

            if user_dict.get("date_of_birth"):
                try:
                    user_dict["dob_decrypted"] = self.crypto.decrypt_data(
                        user_dict["date_of_birth"], context=str(user_id)
                    )
                except Exception:
                    user_dict["dob_decrypted"] = None

            result[user_id] = self._dict_to_user(user_dict)

        return result

    def grant_permission(self, user_id: int, permission: str) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        perms = user.permissions.copy()
        perms[permission] = True
        self.update_user(user_id, permissions=perms)
        return True

    # === OAuth ===

    def has_capability(self, token_info: TokenInfo, capability: str) -> bool:
        """Check if token has a specific capability/permission."""
        return has_permission(token_info.permissions, capability)

    def require_capability(self, token_info: TokenInfo, capability: str) -> None:
        """Require a capability, raising PermissionDeniedError if missing."""
        if not self.has_capability(token_info, capability):
            raise PermissionDeniedError(f"Missing required permission: {capability}")

    def oauth_login(
        self,
        provider: str,
        external_id: str,
        email: Optional[str] = None,
        username_hint: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        age: Optional[int] = None,
        dob: Optional[str] = None,
    ) -> AuthResult:
        """Handle OAuth login flow: verify external ID, link or create user, and start session."""
        # 1. Check if we already have this external account linked
        row = self._db.fetch_one(
            "SELECT user_id FROM auth_external_accounts WHERE provider = ? AND external_id = ?",
            (provider, external_id),
        )

        user_id = None
        if row:
            user_id = row["user_id"]
        else:
            # 2. Not linked. Check if a user with this email already exists to auto-link
            if email:
                email_index = self.crypto.blind_index(email, "user_email")
                user_row = self._db.fetch_one(
                    "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
                )
                if user_row:
                    user_id = user_row["id"]
                    # Link existing user to this provider
                    self._db.insert_or_ignore(
                        "auth_external_accounts",
                        [
                            "id",
                            "user_id",
                            "provider",
                            "external_id",
                            "email_index",
                            "created_at",
                        ],
                        (
                            self._generate_id(),
                            user_id,
                            provider,
                            external_id,
                            email_index,
                            self._get_timestamp(),
                        ),
                    )

        # 3. If still no user, create a new one
        if not user_id:
            # Check age gate before attempting registration
            accounts_config = self._config.get("accounts", {})
            if accounts_config.get("age_gate_enabled", False):
                if age is None and dob is None:
                    # Signal to client that age/DOB is needed for new registration
                    return AuthResult(
                        status=AuthStatus.FAILED, message="Age verification required"
                    )

            # Use username_hint or derive from email/external_id
            base_username = username_hint or (
                email.split("@")[0] if email else f"{provider}_{external_id[:8]}"
            )

            # Ensure username uniqueness
            username = base_username
            attempts = 0
            while attempts < 10 and self._db.fetch_one(
                "SELECT id FROM auth_users WHERE username = ?", (username,)
            ):
                username = f"{base_username}{secrets.token_hex(2)}"
                attempts += 1

            # Use a random strong password for OAuth-only accounts
            random_password = secrets.token_urlsafe(32)

            try:
                user = self.register(
                    username=username,
                    email=email or f"{external_id}@{provider}.internal",
                    password=random_password,
                    ip_address=ip_address,
                    age=age,
                    dob=dob,
                )
                user_id = user.id

                # Link the new user
                email_index = self.crypto.blind_index(
                    email or f"{external_id}@{provider}.internal", "user_email"
                )
                self._db.insert_or_ignore(
                    "auth_external_accounts",
                    [
                        "id",
                        "user_id",
                        "provider",
                        "external_id",
                        "email_index",
                        "created_at",
                    ],
                    (
                        self._generate_id(),
                        user_id,
                        provider,
                        external_id,
                        email_index,
                        self._get_timestamp(),
                    ),
                )
            except AuthError as e:
                # If registration fails (e.g. age gate), we pass that up
                self._log_audit(
                    AuditEventType.LOGIN_FAILED,
                    None,
                    False,
                    ip_address,
                    details={"error": str(e), "provider": provider},
                )
                return AuthResult(status=AuthStatus.FAILED, message=str(e))

        # 4. Success - load user and start session
        user_obj = self.get_user(user_id)
        if not user_obj:
            return AuthResult(status=AuthStatus.FAILED, message="User creation failed")

        if user_obj.account_locked:
            return AuthResult(status=AuthStatus.FAILED, message="Account locked")

        session = self._create_session(user_id, None, ip_address, user_agent)

        # Update last login
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE auth_users SET last_login_at = ?, failed_login_attempts = 0 WHERE id = ?",
            (now, user_id),
        )
        self._db.execute(
            "UPDATE auth_external_accounts SET last_login_at = ? WHERE user_id = ? AND provider = ?",
            (now, user_id, provider),
        )

        self._log_audit(
            AuditEventType.LOGIN_SUCCESS,
            user_id,
            True,
            ip_address,
            details={"provider": provider},
        )

        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user_obj,
            session=session,
        )

    # === Account Deletion ===

    def schedule_account_deletion(self, user_id: int, password: str, totp_code: Optional[str] = None) -> bool:
        """
        Schedules an account for deletion with a 30-day grace period.
        """
        row = self._db.fetch_one("SELECT username, email_encrypted, password_hash, totp_enabled, totp_secret_encrypted FROM auth_users WHERE id = ?", (user_id,))
        if not row:
            raise UserNotFoundError("User not found")

        # 1. Verify password
        if not self.crypto.verify_password(password, row["password_hash"]):
            raise InvalidCredentialsError("Incorrect password")

        # 2. Verify TOTP if enabled
        if row["totp_enabled"]:
            if not totp_code:
                raise TwoFactorInvalidError("2FA code required")
            
            secret = self.crypto.decrypt_data(row["totp_secret_encrypted"], context=str(user_id))
            if not totp_module.verify_totp_code(secret, totp_code, user_id=user_id):
                raise TwoFactorInvalidError("Invalid 2FA code")

        # 3. Perform the freeze
        grace_days = self._config.get("account_deletion", {}).get("grace_period_days", 30)
        now = self._get_timestamp()
        deletion_at = now + (grace_days * 86400)

        self._db.execute(
            "UPDATE auth_users SET deletion_status = 'frozen', deletion_at = ? WHERE id = ?",
            (deletion_at, user_id)
        )

        # 4. Backup to DB record (last resort)
        # Snowflake ID for record
        record_id = self._generate_id()
        # identifier is email or username
        identifier = row["username"]
        import hashlib
        self._db.execute(
            "INSERT INTO auth_deletion_records (id, user_id, identifier_hash, status, scheduled_at) VALUES (?, ?, ?, ?, ?)",
            (record_id, user_id, hashlib.sha256(identifier.encode()).hexdigest(), 'frozen', now)
        )

        # 5. Log to external Hash-Chain Audit Log
        self.deletion_log.log_event(user_id, "SCHEDULED", identifier, {"scheduled_at": now, "deletion_at": deletion_at})

        # 6. Purge all sessions
        self.logout_all(user_id)
        
        # 7. Invalidate caches
        invalidate_pattern(f"user_profile:{user_id}")
        invalidate_pattern(f"user_data:*{user_id}*")
        
        logger.info(f"Account scheduled for deletion: user_id={user_id}, scheduled_at={now}, deletion_at={deletion_at}")
        return True

    def cancel_account_deletion(self, user_id: int, admin_id: Optional[int] = None) -> bool:
        """
        Cancels a scheduled account deletion.
        """
        row = self._db.fetch_one("SELECT username FROM auth_users WHERE id = ?", (user_id,))
        if not row:
            raise UserNotFoundError("User not found")

        self._db.execute(
            "UPDATE auth_users SET deletion_status = 'active', deletion_at = NULL WHERE id = ?",
            (user_id,)
        )
        
        # Update/Delete backup record
        self._db.execute("DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,))

        # Log to external Hash-Chain
        self.deletion_log.log_event(user_id, "CANCELLED", row["username"], {"admin_id": admin_id})

        invalidate_pattern(f"user_profile:{user_id}")
        logger.info(f"Account deletion cancelled: user_id={user_id}, cancelled_by={admin_id or 'user'}")
        return True



