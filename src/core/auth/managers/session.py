import datetime
from typing import Optional, List, Dict

import utils.logger as logger
from src.core.database import cached, invalidate_pattern

from ..exceptions import (
    InvalidCredentialsError,
    AccountLockedError,
    EmailNotVerifiedError,
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)
from ..models import (
    User,
    Session,
    TokenInfo,
    AuthResult,
    AccountType,
    AuditEventType,
    AuthStatus,
)
from ..permissions import permissions_from_json
from ..tokens import create_session_token, parse_token, verify_token_hash


from .protocol import AuthManagerProtocol


class SessionMixin(AuthManagerProtocol):
    def create_session_for_user(
        self,
        user_id: int,
        device_info: Optional[Dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        row = self._db.fetch_one("SELECT * FROM auth_users WHERE id = ?", (user_id,))
        if not row:
            raise UserNotFoundError("User not found")

        if row.get("deletion_status") == "frozen":
            raise AccountLockedError(
                "Account is scheduled for deletion. Contact an administrator to cancel."
            )

        if row["account_locked"]:
            if not row["locked_until"] or row["locked_until"] > self._get_timestamp():
                raise AccountLockedError("Account locked", row["locked_until"])

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

        user = self.get_user(user_id)
        self._log_audit(
            AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
        )
        return AuthResult(
            status=AuthStatus.SUCCESS, token=session.token, user=user, session=session
        )

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

        deletion_status = row.get("deletion_status", "active")
        if deletion_status == "frozen":
            deletion_at = row.get("deletion_at")
            logger.warning(f"Login attempt for frozen account {user_id}")
            try:
                if deletion_at and deletion_at < 32536799999:
                    dt = datetime.datetime.fromtimestamp(deletion_at).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                else:
                    dt = "a future date"
            except (ValueError, OSError, OverflowError):
                dt = "a future date"
            raise AccountLockedError(
                f"Account is scheduled for deletion on {dt}. Contact an administrator to cancel."
            )

        if row["account_locked"]:
            if not row["locked_until"] or row["locked_until"] > self._get_timestamp():
                raise AccountLockedError("Account locked", row["locked_until"])

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
                email = self.crypto.decrypt_data(
                    row["email_encrypted"], context=str(user_id)
                )
            except Exception:
                email = "[decryption failed]"

        dob = None
        if row["date_of_birth"]:
            try:
                dob = self.crypto.decrypt_data(
                    row["date_of_birth"], context=str(user_id)
                )
            except Exception:
                dob = "[decryption failed]"

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
            date_of_birth=dob,
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

        security_config = self._config.get("security", {})
        threshold = security_config.get("max_failed_attempts", 5)

        row = self._db.fetch_one(
            "SELECT failed_login_attempts FROM auth_users WHERE id = ?", (user_id,)
        )

        if row and row["failed_login_attempts"] >= threshold:
            lock_duration_min = security_config.get("lockout_duration_minutes", 15)
            lock_until = self._get_timestamp() + (lock_duration_min * 60 * 1000)
            self._db.execute(
                "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
                (lock_until, user_id),
            )
            self._log_audit(AuditEventType.ACCOUNT_LOCKED, user_id, True, ip_address)
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "account_locked")
            except Exception:
                pass

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
        sessions_config = self._config.get("sessions", {})
        expire_hours = sessions_config.get("expire_hours", 720)
        expires = now + (expire_hours * 3600 * 1000)
        token, token_hash = create_session_token(sid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(sid))

        ua_index = self._ua_index(ua)
        ua_encrypted = self._encrypt_ua(ua, str(sid))

        max_sessions = sessions_config.get("max_per_user", 10)
        sessions = self.get_sessions(user_id)
        if len(sessions) >= max_sessions:
            sessions.sort(key=lambda x: x.last_activity)
            to_revoke = len(sessions) - max_sessions + 1
            for i in range(to_revoke):
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
                    (sessions[i].id,),
                )

        self._db.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, device_id, ip_index, ip_encrypted, ua_index, ua_encrypted, created_at, expires_at, last_activity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua_index,
                ua_encrypted,
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
        ttl=10,
        prefix="token_verify",
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            bool(
                self._config.get("security", {}).get("token_binding", False)
                or self._config.get("security", {}).get("token_verify_rate_limit")
            )
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
        if rate_limit and ip_address and not is_selftest:
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

        if row.get("deletion_status") == "frozen":
            logger.warning(f"Attempted use of token for frozen user {row['user_id']}")
            raise TokenInvalidError("Account is scheduled for deletion")

        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")

            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if row.get("ip_index") and row["ip_index"] != current_ip_index:
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")

            current_ua_index = self._ua_index(user_agent)
            if row.get("ua_index") and row["ua_index"] != current_ua_index:
                raise TokenInvalidError("User-Agent Binding Mismatch")

        now = self._get_timestamp()

        last_activity = row.get("last_activity", 0)
        should_update = (now - last_activity) > 60000
        sessions_config = self._config.get("sessions", {})
        if sessions_config.get("extend_on_activity", True):
            extend_threshold_hours = sessions_config.get("extend_threshold_hours", 24)
            extend_threshold = extend_threshold_hours * 3600 * 1000
            if (
                extend_threshold_hours == 0
                or (row["expires_at"] - now) <= extend_threshold
            ):
                should_update = True

        if should_update:
            updates = ["last_activity = ?"]
            params = [now]

            if sessions_config.get("extend_on_activity", True):
                extend_threshold_hours = sessions_config.get(
                    "extend_threshold_hours", 24
                )
                extend_threshold = extend_threshold_hours * 3600 * 1000
                if (
                    extend_threshold_hours == 0
                    or (row["expires_at"] - now) <= extend_threshold
                ):
                    expire_hours = sessions_config.get("expire_hours", 720)
                    new_expires = now + (expire_hours * 3600 * 1000)
                    updates.append("expires_at = ?")
                    params.append(new_expires)

            params.append(row["id"])
            self._db.execute(
                f"UPDATE auth_sessions SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

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
        now = self._get_timestamp()
        expire_hours = self._config.get("sessions", {}).get("expire_hours", 720)
        new_expires = now + (expire_hours * 3600 * 1000)
        self._db.execute(
            "UPDATE auth_sessions SET expires_at = ?, last_activity = ? WHERE id = ?",
            (new_expires, now, parsed["id"]),
        )
        invalidate_pattern("token_verify:*")
        return token

    def logout(self, token: str) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            row = self._db.fetch_one(
                "SELECT user_id FROM auth_sessions WHERE id = ?", (parsed["id"],)
            )
            if row:
                invalidate_pattern("token_verify:*")
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
                )
                self._log_audit(AuditEventType.LOGOUT, row["user_id"], True)
                return True
        return False

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
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
                try:
                    from src.core.events.gateway_emit import emit_security_alert

                    emit_security_alert(user_id, "session_revoked")
                except Exception:
                    pass
                return count_row["cnt"] if count_row else 0
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "session_revoked")
        except Exception:
            pass
        return count_row["cnt"] if count_row else 0

    def logout_all_users(self) -> int:
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

            ua = None
            if r.get("ua_encrypted"):
                try:
                    ua = self.crypto.decrypt_data(
                        r["ua_encrypted"], context=str(r["id"])
                    )
                except Exception:
                    ua = "[decryption failed]"

            sessions.append(
                Session(
                    id=r["id"],
                    user_id=r["user_id"],
                    device_id=r["device_id"],
                    ip_address=ip_address,
                    user_agent=ua,
                    created_at=r["created_at"],
                    expires_at=r["expires_at"],
                    last_activity=r["last_activity"],
                    revoked=bool(r["revoked"]),
                )
            )
        return sessions

    def revoke_session(self, user_id: int, session_id: int) -> bool:
        invalidate_pattern("token_verify:*")
        cursor = self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        if cursor.rowcount > 0:
            self._log_audit(AuditEventType.SESSION_REVOKED, user_id, True)
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "session_revoked")
            except Exception:
                pass
            return True
        return False
