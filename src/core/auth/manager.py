"""
Hardened AuthManager - Secure authentication logic.
"""

import time
import json
import secrets
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger

from src.core.base import BaseManager, SnowflakeID
from src.utils.encryption import EncryptionManager

from .models import (
    User,
    Session,
    Bot,
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
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorInvalidError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
    InvalidUsernameError,
    InvalidEmailError,
)
from .permissions import (
    DEFAULT_USER_PERMISSIONS,
    DEFAULT_BOT_PERMISSIONS,
    permissions_to_json,
    permissions_from_json,
)
from .schema import create_tables
from .tokens import (
    create_session_token,
    create_bot_token,
    create_email_token,
    create_2fa_challenge_token,
    parse_token,
    verify_token_hash,
)
from .passwords import (
    validate_password as validate_pwd,
    validate_username,
    validate_email,
)
from . import totp as totp_module


class AuthManager(BaseManager):
    def __init__(self, db, email_sender=None):
        super().__init__(db)
        self.email_sender = email_sender
        self.crypto = EncryptionManager()
        self._config = config.get("authentication", {})
        logger.info("Initializing authentication module")
        create_tables(db)
        self._ensure_system_user()

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
        self._db.insert_or_ignore(
            "auth_audit_log",
            [
                "id",
                "user_id",
                "event_type",
                "ip_address",
                "device_id",
                "timestamp",
                "details_encrypted",
                "success",
            ],
            (
                audit_id,
                user_id,
                event_type.value,
                ip_address,
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
                # Basic DOB validation (calculate age from YYYY-MM-DD)
                try:
                    from datetime import datetime
                    birth_date = datetime.strptime(dob, "%Y-%m-%d")
                    today = datetime.today()
                    calculated_age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
                    if calculated_age < min_age:
                        raise AuthError(f"Minimum age requirement not met ({min_age})", "age")
                    age_verified = 1
                    # Encrypt DOB for storage using user_id as context
                    stored_dob = self.crypto.encrypt_data(dob, context=str(user_id))
                except ValueError:
                    raise AuthError("Invalid date format. Use YYYY-MM-DD", "dob")
            else:
                # Boolean/Age mode
                if age is None:
                    raise AuthError("Age is required", "age")
                if age < min_age:
                    raise AuthError(f"Minimum age requirement not met ({min_age})", "age")
                age_verified = 1
                stored_dob = None # Do not store DOB in boolean mode
        
        valid, issues = validate_username(username)
        if not valid:
            raise InvalidUsernameError(f"Invalid: {issues}", issues)
        if not validate_email(email):
            raise InvalidEmailError("Invalid email")

        pwd_val = validate_pwd(password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)

        if self._db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        ):
            raise UserExistsError("Username taken", "username")

        email_index = self.crypto.blind_index(email, "user_email")
        if self._db.fetch_one(
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        ):
            raise UserExistsError("Email registered", "email")

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
            date_of_birth=dob if (age_gate_enabled and verification_type == "dob") else None,
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
            return False
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
        if row["account_locked"]:
            if row["locked_until"] and row["locked_until"] > self._get_timestamp():
                raise AccountLockedError("Account locked", row["locked_until"])
            self._db.execute(
                "UPDATE auth_users SET account_locked = 0, failed_login_attempts = 0 WHERE id = ?",
                (user_id,),
            )

        if not self.crypto.verify_password(password, row["password_hash"]):
            self._handle_failed_login(user_id, ip_address)
            raise InvalidCredentialsError("Invalid credentials")

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

        email = (
            self.crypto.decrypt_data(row["email_encrypted"], context=str(user_id))
            if row["email_encrypted"]
            else None
        )
        user = User(
            id=user_id,
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=email,
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        self._log_audit(
            AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
        )
        return AuthResult(
            status=AuthStatus.SUCCESS, token=session.token, user=user, session=session
        )

    def _handle_failed_login(self, user_id: int, ip_address: Optional[str]):
        self._db.execute(
            "UPDATE auth_users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )
        row = self._db.fetch_one(
            "SELECT failed_login_attempts FROM auth_users WHERE id = ?", (user_id,)
        )
        if row and row["failed_login_attempts"] >= 5:
            lock_until = self._get_timestamp() + 900000
            self._db.execute(
                "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
                (lock_until, user_id),
            )
            self._log_audit(AuditEventType.ACCOUNT_LOCKED, user_id, True, ip_address)

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
        expire_hours = self._config.get("sessions.expire_hours", 720)
        expires = now + (expire_hours * 3600 * 1000)
        token, token_hash = create_session_token(sid)
        self._db.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, device_id, ip_address, user_agent, created_at, expires_at, last_activity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, user_id, token_hash, device_id, ip, ua, now, expires, now),
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

    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        parsed = parse_token(token)
        if not parsed:
            raise TokenInvalidError("Invalid token")
        if parsed["token_type"] == "session":
            return self._verify_session_token(parsed, ip_address)
        elif parsed["token_type"] == "bot":
            return self._verify_bot_token(parsed, ip_address)
        raise TokenInvalidError("Unknown type")

    def _verify_session_token(
        self, parsed: Dict, ip_address: Optional[str]
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT s.*, u.username, u.permissions, u.account_type FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
            (parsed["id"],),
        )
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid/Revoked")
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")
        if (
            self._get_config("security.token_binding", False)
            and ip_address
            and row["ip_address"] != ip_address
        ):
            raise TokenInvalidError("IP Binding Mismatch")
        now = self._get_timestamp()
        updates = ["last_activity = ?"]
        params = [now]

        # Extend session if enabled
        if self._config.get("sessions.extend_on_activity", True):
            extend_threshold = self._config.get("sessions.extend_threshold_hours", 24) * 3600 * 1000
            if (row["expires_at"] - now) < extend_threshold:
                expire_hours = self._config.get("sessions.expire_hours", 720)
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
        return token

    def logout(self, token: str) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            self._db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
            )
            return True
        return False

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
        if except_token:
            parsed = parse_token(except_token)
            if parsed and parsed["token_type"] == "session":
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND id != ?",
                    (user_id, parsed["id"]),
                )
                return 1
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        return 1

    def get_sessions(self, user_id: int) -> List[Session]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_sessions WHERE user_id = ? AND revoked = 0", (user_id,)
        )
        return [
            Session(
                id=r["id"],
                user_id=r["user_id"],
                device_id=r["device_id"],
                ip_address=r["ip_address"],
                user_agent=r["user_agent"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
                last_activity=r["last_activity"],
                revoked=bool(r["revoked"]),
            )
            for r in rows
        ]

    def revoke_session(self, user_id: int, session_id: int) -> bool:
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        return True

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
            updates.append("username = ?")
            params.append(username)
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
        self._db.execute(
            "INSERT INTO auth_2fa_challenges (id, user_id, token_hash, device_id, ip_address, user_agent, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, user_id, token_hash, device_id, ip, ua, now, expires),
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
        if not row or row["used"] or row["expires_at"] < self._get_timestamp():
            raise TokenInvalidError("Expired/Used")

        user_row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE id = ?", (row["user_id"],)
        )
        if not user_row:
            raise UserNotFoundError("User not found")

        user_id = user_row["id"]
        secret = totp_module.decrypt_totp_secret(user_row["totp_secret_encrypted"])
        
        # Try TOTP code first (with replay prevention)
        if not totp_module.verify_totp_code(secret, code, user_id=user_id):
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
        session = self._create_session(
            user_id, row["device_id"], row["ip_address"], row["user_agent"]
        )
        email = (
            self.crypto.decrypt_data(
                user_row["email_encrypted"], context=str(user_id)
            )
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
            qr_uri=totp_module.generate_totp_uri(secret, user["username"], "PlexiChat"),
            backup_codes=backup_codes,
            issuer="PlexiChat",
            username=user["username"],
        )

    def confirm_2fa(self, user_id: int, code: str) -> bool:
        user = self._db.fetch_one(
            "SELECT totp_secret_encrypted FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            return False
        # Use user_id for replay prevention during confirmation
        if totp_module.verify_totp_code(
            totp_module.decrypt_totp_secret(user["totp_secret_encrypted"]), 
            code,
            user_id=user_id
        ):
            self._db.execute(
                "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?", (user_id,)
            )
            return True
        return False

    def disable_2fa(self, user_id: int, password: str, code: str) -> bool:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_secret_encrypted FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            return False
        if not self.crypto.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        # Use user_id for replay prevention
        if not totp_module.verify_totp_code(
            totp_module.decrypt_totp_secret(user["totp_secret_encrypted"]), 
            code,
            user_id=user_id
        ):
            raise TwoFactorInvalidError("Invalid code")
        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_hash = NULL WHERE id = ?",
            (user_id,),
        )
        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        user = self._db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
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
            return TwoFactorStatus(enabled=False, backup_codes_remaining=0)
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
            return False
        if not self.crypto.verify_password(old, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        pwd_val = validate_pwd(new)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        self._db.execute(
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new), user_id),
        )
        return True

    def request_password_reset(self, email: str) -> bool:
        email_index = self.crypto.blind_index(email, "user_email")
        user = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        )
        if not user or not self.email_sender:
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
        bot_id = self._generate_id()
        token, token_hash = create_bot_token(bot_id)
        perms = permissions if permissions is not None else DEFAULT_BOT_PERMISSIONS
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
        token, token_hash = create_bot_token(bot_id)
        self._db.execute(
            "UPDATE auth_bots SET token_hash = ? WHERE id = ? AND owner_id = ?",
            (token_hash, bot_id, owner_id),
        )
        return token

    def update_bot_permissions(
        self, owner_id: int, bot_id: int, permissions: Dict[str, bool]
    ) -> Bot:
        self._db.execute(
            "UPDATE auth_bots SET permissions = ? WHERE id = ? AND owner_id = ?",
            (permissions_to_json(permissions), bot_id, owner_id),
        )
        bot = self.get_bot(bot_id)
        if not bot:
            raise AuthError("Bot not found")
        return bot

    def disable_bot(self, owner_id: int, bot_id: int) -> bool:
        self._db.execute(
            "UPDATE auth_bots SET disabled = 1 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        return True

    def enable_bot(self, owner_id: int, bot_id: int) -> bool:
        self._db.execute(
            "UPDATE auth_bots SET disabled = 0 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        return True

    def delete_bot(self, owner_id: int, bot_id: int) -> bool:
        self._db.execute(
            "DELETE FROM auth_bots WHERE id = ? AND owner_id = ?", (bot_id, owner_id)
        )
        return True

    # === Device Management ===

    def _track_device(self, user_id: int, info: Dict) -> int:
        fp = info.get("fingerprint", "unknown")
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
        self._db.execute(
            "UPDATE auth_devices SET name = ? WHERE id = ? AND user_id = ?",
            (name, device_id, user_id),
        )
        return True

    def revoke_device(self, user_id: int, device_id: int) -> bool:
        self._db.execute(
            "DELETE FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        return True

    # === IP Tracking ===

    def _track_ip(self, user_id: int, ip: str):
        self._db.insert_or_ignore(
            "auth_known_ips",
            ["id", "user_id", "ip_address", "first_seen_at", "last_seen_at"],
            (
                self._generate_id(),
                user_id,
                ip,
                self._get_timestamp(),
                self._get_timestamp(),
            ),
        )

    # === Audit & History ===

    def get_login_history(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_audit_log WHERE user_id = ? AND event_type IN (?, ?) ORDER BY timestamp DESC LIMIT ?",
            (
                user_id,
                AuditEventType.LOGIN_SUCCESS.value,
                AuditEventType.LOGIN_FAILED.value,
                limit,
            ),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=r["ip_address"],
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
                ip_address=r["ip_address"],
                device_id=r["device_id"],
                timestamp=r["timestamp"],
                details=None,
                success=bool(r["success"]),
            )
            for r in rows
        ]

    # === Utility ===

    def get_user(self, user_id: int) -> Optional[User]:
        row = self._db.fetch_one("SELECT * FROM auth_users WHERE id = ?", (user_id,))
        if not row:
            return None
            
        email = None
        if row["email_encrypted"]:
            try:
                email = self.crypto.decrypt_data(row["email_encrypted"], context=str(user_id))
            except Exception as e:
                logger.error(f"Decryption failed for user {user_id}: {e}")
                # Don't return a magic string that could be treated as a valid email
                email = None
        
        dob = None
        if row["date_of_birth"]:
            try:
                # Decrypt DOB using user_id as context
                dob = self.crypto.decrypt_data(row["date_of_birth"], context=str(user_id))
            except Exception:
                # Fallback or silent failure for PII
                pass

        return User(
            id=row["id"],
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=email,
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            email_verified=bool(row["email_verified"]),
            account_locked=bool(row["account_locked"]),
            totp_enabled=bool(row["totp_enabled"]),
            age_verified=bool(row["age_verified"]),
            date_of_birth=row["date_of_birth"],
        )

    def get_user_by_username(self, username: str) -> Optional[User]:
        row = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        )
        if not row:
            return None
        return self.get_user(row["id"])

    def get_users_bulk(self, user_ids: List[int]) -> Dict[int, User]:
        if not user_ids:
            return {}
        
        placeholders = ",".join("?" for _ in user_ids)
        rows = self._db.fetch_all(
            f"SELECT * FROM auth_users WHERE id IN ({placeholders})", tuple(user_ids)
        )
        
        result = {}
        for row in rows:
            user_id = row["id"]
            email = None
            if row["email_encrypted"]:
                try:
                    email = self.crypto.decrypt_data(row["email_encrypted"], context=str(user_id))
                except Exception as e:
                    logger.error(f"Decryption failed for user {user_id} in bulk fetch: {e}")
            
            dob = None
            if row["date_of_birth"]:
                try:
                    dob = self.crypto.decrypt_data(row["date_of_birth"], context=str(user_id))
                except Exception:
                    pass

            result[user_id] = User(
                id=user_id,
                account_type=AccountType(row["account_type"]),
                username=row["username"],
                email=email,
                permissions=permissions_from_json(row["permissions"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                email_verified=bool(row["email_verified"]),
                account_locked=bool(row["account_locked"]),
                totp_enabled=bool(row["totp_enabled"]),
                age_verified=bool(row["age_verified"]),
                date_of_birth=dob,
            )
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
                        ["id", "user_id", "provider", "external_id", "email_index", "created_at"],
                        (self._generate_id(), user_id, provider, external_id, email_index, self._get_timestamp())
                    )

        # 3. If still no user, create a new one
        if not user_id:
            # Check age gate before attempting registration
            accounts_config = self._config.get("accounts", {})
            if accounts_config.get("age_gate_enabled", False):
                if age is None and dob is None:
                    # Signal to client that age/DOB is needed for new registration
                    return AuthResult(status=AuthStatus.FAILED, message="Age verification required")

            # Use username_hint or derive from email/external_id
            base_username = username_hint or (email.split("@")[0] if email else f"{provider}_{external_id[:8]}")
            
            # Ensure username uniqueness
            username = base_username
            attempts = 0
            while attempts < 10 and self._db.fetch_one("SELECT id FROM auth_users WHERE username = ?", (username,)):
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
                    dob=dob
                )
                user_id = user.id
                
                # Link the new user
                email_index = self.crypto.blind_index(email or f"{external_id}@{provider}.internal", "user_email")
                self._db.insert_or_ignore(
                    "auth_external_accounts",
                    ["id", "user_id", "provider", "external_id", "email_index", "created_at"],
                    (self._generate_id(), user_id, provider, external_id, email_index, self._get_timestamp())
                )
            except AuthError as e:
                # If registration fails (e.g. age gate), we pass that up
                self._log_audit(AuditEventType.LOGIN_FAILED, None, False, ip_address, details={"error": str(e), "provider": provider})
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
            (now, user_id)
        )
        self._db.execute(
            "UPDATE auth_external_accounts SET last_login_at = ? WHERE user_id = ? AND provider = ?",
            (now, user_id, provider)
        )

        self._log_audit(AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, details={"provider": provider})
        
        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user_obj,
            session=session
        )
