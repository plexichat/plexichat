import datetime
import hashlib
from typing import Optional, List, Dict, Tuple

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
from ..tokens import (
    create_session_token,
    create_ws_ticket,
    parse_token,
    verify_token_hash,
)


from .protocol import AuthManagerProtocol

# CORRECTNESS FIX: verify_token is wrapped in @cached, so a cache hit
# reconstructs its return value from JSON. Without registering TokenInfo on
# the reconstruction allowlist, a hit returns a PLAIN DICT and every consumer
# that touches token_info.user_id / account_locked crashes (e.g. the second
# authenticated request within the 10s verify TTL failing with a 500).
from src.core.database.cache.serialization import register_reconstructable_type

register_reconstructable_type(TokenInfo)


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
            # Trusted-device 2FA skip: a device this account has explicitly
            # marked trusted bypasses the challenge. SECURITY: the check is on
            # the tracked device fingerprint (server-side), not on any client
            # claim, and a missing fingerprint can never match.
            fp = (device_info or {}).get("fingerprint")
            if fp and self.is_trusted_device(user_id, fp):
                session = self._create_session(
                    user_id, device_id, ip_address, user_agent
                )
                self._db.execute(
                    "UPDATE auth_users SET failed_login_attempts = 0, last_login_at = ? WHERE id = ?",
                    (self._get_timestamp(), user_id),
                )
                self._clear_failed_login_state(user_id)
                self._log_audit(
                    AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
                )
                user = self.get_user(user_id)
                return AuthResult(
                    status=AuthStatus.SUCCESS,
                    token=session.token,
                    user=user,
                    session=session,
                )
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
        self._clear_failed_login_state(user_id)

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
            # Equalize timing with the found-user path so responses are identical
            # regardless of whether the account exists (prevents enumeration).
            self.crypto.verify_password(password, self._dummy_password_hash)
            self._log_audit(AuditEventType.LOGIN_FAILED, None, False, ip_address)
            raise InvalidCredentialsError("Invalid credentials")

        user_id = row["id"]

        # Per-(user, IP) throttle: this source has burned its failure budget,
        # so refuse further attempts (uniformly, to avoid enumeration) even
        # before touching the password. The account itself stays usable from
        # other IPs — only a distributed pattern triggers the account lock.
        self._ensure_failed_logins_table()
        if ip_address:
            ip_index = self.crypto.blind_index(ip_address, "ip_address")
            if self._ip_throttle_active(user_id, ip_index):
                self.crypto.verify_password(password, self._dummy_password_hash)
                logger.warning(
                    f"Login throttled for user {user_id} from blinded source IP"
                )
                raise InvalidCredentialsError("Invalid credentials")

        # Verify the password BEFORE any account-state checks so that locked or
        # frozen accounts are indistinguishable (in timing and response shape)
        # from valid accounts with wrong passwords, preventing enumeration.
        if not self.crypto.verify_password(password, row["password_hash"]):
            self._log_audit(AuditEventType.LOGIN_FAILED, user_id, False, ip_address)
            self._handle_failed_login(user_id, ip_address)
            raise InvalidCredentialsError("Invalid credentials")

        # Transparent upgrade for legacy / stale-pepper hashes. The plaintext
        # is proven correct at this point, so rehash with the current pepper
        # and Argon2 parameters. Best-effort: never fail a good login.
        try:
            if self.crypto.needs_rehash(row["password_hash"]):
                self._db.execute(
                    "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                    (self.crypto.hash_password(password), user_id),
                )
        except Exception:
            pass

        deletion_status = row.get("deletion_status", "active")
        if deletion_status == "frozen":
            deletion_at = row.get("deletion_at")
            logger.warning(f"Login attempt for frozen account {user_id}")
            try:
                # deletion_at is stored in MILLISECONDS; the sanity bound is
                # therefore also ms (32536799999 s ~= year 3001, * 1000).
                # Comparing ms against a seconds bound (or passing ms to
                # fromtimestamp) always fell through to "a future date".
                if deletion_at and deletion_at < 32536799999 * 1000:
                    # Render the deletion deadline in UTC so the notice is
                    # identical regardless of the server's local timezone.
                    dt = datetime.datetime.fromtimestamp(
                        deletion_at / 1000, tz=datetime.timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S UTC")
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

        accounts_config = self._config.get("accounts", {})
        if accounts_config.get("require_email_verification", False) and not bool(
            row.get("email_verified", 0)
        ):
            raise EmailNotVerifiedError("Email not verified")

        device_id = self._track_device(user_id, device_info) if device_info else None
        if ip_address:
            self._track_ip(user_id, ip_address)

        if row["totp_enabled"]:
            # Trusted-device 2FA skip: a device this account has explicitly
            # marked trusted bypasses the challenge. SECURITY: the check is on
            # the tracked device fingerprint (server-side), not on any client
            # claim, and a missing fingerprint can never match.
            fp = (device_info or {}).get("fingerprint")
            if fp and self.is_trusted_device(user_id, fp):
                session = self._create_session(
                    user_id, device_id, ip_address, user_agent
                )
                self._db.execute(
                    "UPDATE auth_users SET failed_login_attempts = 0, last_login_at = ? WHERE id = ?",
                    (self._get_timestamp(), user_id),
                )
                self._clear_failed_login_state(user_id)
                self._log_audit(
                    AuditEventType.LOGIN_SUCCESS, user_id, True, ip_address, device_id
                )
                user = self.get_user(user_id)
                return AuthResult(
                    status=AuthStatus.SUCCESS,
                    token=session.token,
                    user=user,
                    session=session,
                )
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
        # Successful authentication clears the failure bookkeeping so stale
        # (user, IP) counters cannot throttle or lock later on.
        self._clear_failed_login_state(user_id)

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

    # DDL bookkeeping for the failed-login tracking table (DDL itself lives in
    # schema.py — single source of truth). The lazy guard covers databases
    # created before that schema entry existed without paying the cost on
    # every failure.
    _failed_logins_table_ready: bool = False
    _failed_logins_last_cleanup: int = 0

    def _ensure_failed_logins_table(self) -> None:
        if SessionMixin._failed_logins_table_ready:
            return
        from ..schema import ensure_failed_logins_table

        ensure_failed_logins_table(self._db)
        SessionMixin._failed_logins_table_ready = True

    def _ip_throttle_active(self, user_id: int, ip_index: str) -> bool:
        """True when this (user, IP) pair has exhausted its failure budget.

        A single source hammering one account gets throttled for itself while
        the account stays usable from everywhere else — targeted lockout DoS
        requires the distributed pattern handled in ``_handle_failed_login``.
        """
        security_config = self._config.get("security", {})
        threshold = security_config.get("max_failed_attempts", 5)
        window_minutes = int(security_config.get("failed_login_window_minutes", 15))
        # Budget before this IP is throttled: double the lockout threshold.
        throttle_limit = max(1, int(threshold)) * 2 if threshold else 0
        if not throttle_limit:
            return False
        row = self._db.fetch_one(
            "SELECT attempts, last_attempt_at FROM auth_failed_logins WHERE user_id = ? AND ip_index = ?",
            (user_id, ip_index),
        )
        if not row:
            return False
        cutoff = self._get_timestamp() - window_minutes * 60 * 1000
        if row.get("last_attempt_at", 0) <= cutoff:
            return False
        return bool(row.get("attempts", 0) >= throttle_limit)

    def _clear_failed_login_state(self, user_id: int) -> None:
        """Reset failure bookkeeping after a successful authentication."""
        self._ensure_failed_logins_table()
        self._db.execute("DELETE FROM auth_failed_logins WHERE user_id = ?", (user_id,))

    def _handle_failed_login(self, user_id: int, ip_address: Optional[str]):
        security_config = self._config.get("security", {})
        threshold = security_config.get("max_failed_attempts", 5)
        # A single source IP must not be able to deny service to a known
        # username by brute-forcing it: account lockout additionally requires
        # failures from several distinct IPs within the window (or an absent
        # IP, which falls back to the legacy behavior).
        min_distinct_ips = int(security_config.get("lockout_min_distinct_ips", 2))
        window_minutes = int(security_config.get("failed_login_window_minutes", 15))
        window_ms = window_minutes * 60 * 1000

        self._ensure_failed_logins_table()

        # Atomic increment: no read-modify-write race between concurrent
        # requests can lose or double-count a failure.
        self._db.execute(
            "UPDATE auth_users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )

        distinct_ips = 0
        now = self._get_timestamp()
        if ip_address:
            ip_index = self.crypto.blind_index(ip_address, "ip_address")
            row = self._db.fetch_one(
                "SELECT attempts FROM auth_failed_logins WHERE user_id = ? AND ip_index = ?",
                (user_id, ip_index),
            )
            if row:
                self._db.execute(
                    "UPDATE auth_failed_logins SET attempts = attempts + 1, last_attempt_at = ? WHERE user_id = ? AND ip_index = ?",
                    (now, user_id, ip_index),
                )
            else:
                try:
                    self._db.execute(
                        "INSERT INTO auth_failed_logins (user_id, ip_index, attempts, last_attempt_at) VALUES (?, ?, 1, ?)",
                        (user_id, ip_index, now),
                    )
                except Exception:
                    # Concurrent insert for the same (user, IP); count it.
                    self._db.execute(
                        "UPDATE auth_failed_logins SET attempts = attempts + 1, last_attempt_at = ? WHERE user_id = ? AND ip_index = ?",
                        (now, user_id, ip_index),
                    )

            count_row = self._db.fetch_one(
                "SELECT COUNT(DISTINCT ip_index) AS c FROM auth_failed_logins WHERE user_id = ? AND last_attempt_at > ?",
                (user_id, now - window_ms),
            )
            distinct_ips = count_row["c"] if count_row else 0

            # Opportunistic cleanup of stale rows (at most once per hour).
            if now - SessionMixin._failed_logins_last_cleanup > 3600 * 1000:
                SessionMixin._failed_logins_last_cleanup = now
                self._db.execute(
                    "DELETE FROM auth_failed_logins WHERE last_attempt_at < ?",
                    (now - 24 * 3600 * 1000,),
                )

        should_lock = threshold is not None and (
            (distinct_ips >= min_distinct_ips) or not ip_address
        )
        if should_lock:
            lock_duration_min = security_config.get("lockout_duration_minutes", 15)
            lock_until = self._get_timestamp() + (lock_duration_min * 60 * 1000)
            # Atomic lock transition: the conditional update guarantees only
            # one request flips the lock and sends the alert, even under
            # concurrency.
            cursor = self._db.execute(
                "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ? AND account_locked = 0 AND failed_login_attempts >= ?",
                (lock_until, user_id, threshold),
            )
            if getattr(cursor, "rowcount", 0) > 0:
                self._log_audit(
                    AuditEventType.ACCOUNT_LOCKED, user_id, True, ip_address
                )
                try:
                    from src.core.events.gateway_emit import emit_security_alert

                    emit_security_alert(user_id, "account_locked")
                except Exception:
                    pass

        invalidate_pattern("user_data:*")

    def _session_expire_hours(
        self,
        user_id: int,
        device_id: Optional[int],
        sessions_config: Dict,
    ) -> int:
        """Resolve the session TTL, honoring per-trust durations.

        Sessions on a trusted device (see auth_devices.trusted) use
        ``trusted_expire_hours`` when configured; untrusted or unknown-device
        sessions always use ``expire_hours``. A null/absent override disables
        the trusted duration entirely, making the feature opt-in per deploy.
        """
        if (
            device_id
            and sessions_config.get("trusted_expire_hours") is not None
            and self._device_is_trusted_by_id(user_id, device_id)
        ):
            return int(sessions_config["trusted_expire_hours"])
        return int(sessions_config.get("expire_hours", 720))

    def _device_is_trusted_by_id(self, user_id: int, device_id: int) -> bool:
        """Trust lookup keyed by the session's tracked device id (server-side
        row), never by a client-supplied fingerprint."""
        self._ensure_trusted_device_columns()
        row = self._db.fetch_one(
            "SELECT trusted FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        return bool(row and row.get("trusted"))

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
        expire_hours = self._session_expire_hours(user_id, device_id, sessions_config)
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
            # Evicted sessions must not remain verifiable via the verify cache.
            invalidate_pattern("token_verify:*")

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
        # Never embed the raw bearer token in the cache key: hash it so that
        # anyone with cache visibility cannot recover live session tokens.
        key_builder=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            f"token_verify:str:{hashlib.sha256(token.encode()).hexdigest()}"
        ),
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            (
                # Single-use WS tickets must NEVER be served from cache: a replay
                # within the TTL would bypass the atomic consumption below.
                isinstance(token, str) and token.startswith("wst.")
            )
            or bool(
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
        elif parsed["token_type"] == "wst":
            return self._verify_ws_ticket(parsed, ip_address, user_agent=user_agent)
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
                # Trusted-device sessions re-arm to their own (longer)
                # duration with their own threshold. Fallback chain:
                # trusted_extend_threshold_hours -> trusted_expire_hours ->
                # the untrusted defaults.
                trusted = bool(
                    row.get("device_id")
                    and self._device_is_trusted_by_id(row["user_id"], row["device_id"])
                )
                if trusted:
                    if (
                        sessions_config.get("trusted_extend_threshold_hours")
                        is not None
                    ):
                        extend_threshold_hours = int(
                            sessions_config["trusted_extend_threshold_hours"]
                        )
                    elif sessions_config.get("trusted_expire_hours") is not None:
                        extend_threshold_hours = int(
                            sessions_config["trusted_expire_hours"]
                        )
                extend_threshold = extend_threshold_hours * 3600 * 1000
                if (
                    extend_threshold_hours == 0
                    or (row["expires_at"] - now) <= extend_threshold
                ):
                    if (
                        trusted
                        and sessions_config.get("trusted_expire_hours") is not None
                    ):
                        expire_hours = int(sessions_config["trusted_expire_hours"])
                    else:
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

    # ------------------------------------------------------------------
    # WebSocket gateway tickets
    #
    # Short-lived, single-use credentials (``wst.{id}.{secret}``) minted for
    # an authenticated session so the long-lived bearer token never has to be
    # handed to the gateway handshake.
    # ------------------------------------------------------------------

    WS_TICKET_TTL_MS = 60 * 1000

    def create_ws_ticket_for_session(
        self,
        user_id: int,
        session_id: Optional[int],
    ) -> Tuple[str, int]:
        """Mint a single-use gateway ticket bound to ``session_id``.

        Returns ``(ticket_token, expires_in_seconds)``.
        """
        self._ensure_ws_tickets_table()
        tid = self._generate_id()
        now = self._get_timestamp()
        ttl_seconds = int(self.WS_TICKET_TTL_MS // 1000)
        token, token_hash = create_ws_ticket(tid)
        self._db.execute(
            "INSERT INTO auth_ws_tickets (id, user_id, session_id, ticket_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid,
                user_id,
                session_id,
                token_hash,
                now,
                now + self.WS_TICKET_TTL_MS,
            ),
        )
        return token, ttl_seconds

    _ws_tickets_table_ready: bool = False

    def _ensure_ws_tickets_table(self) -> None:
        if SessionMixin._ws_tickets_table_ready:
            return
        from ..schema import ensure_ws_tickets_table

        ensure_ws_tickets_table(self._db)
        SessionMixin._ws_tickets_table_ready = True

    def _verify_ws_ticket(
        self,
        parsed: Dict,
        ip_address: Optional[str],
        user_agent: Optional[str] = None,
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT * FROM auth_ws_tickets WHERE id = ?", (parsed["id"],)
        )
        if not row or not verify_token_hash(parsed["secret"], row["ticket_hash"]):
            raise TokenInvalidError("Invalid ticket")
        if row["used"]:
            raise TokenInvalidError("Ticket already used")
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Ticket expired")

        # Atomic single-use consumption: concurrent replays of the same
        # ticket cannot both pass this gate.
        cursor = self._db.execute(
            "UPDATE auth_ws_tickets SET used = 1 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            logger.warning(f"WS ticket replay detected (id={row['id']})")
            raise TokenInvalidError("Ticket already used")

        srow = (
            self._db.fetch_one(
                "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, "
                "u.force_username_change, u.deletion_status "
                "FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
                (row["session_id"],),
            )
            if row["session_id"]
            else None
        )
        if (
            not srow
            or srow["revoked"]
            or srow["expires_at"] < self._get_timestamp()
            or srow.get("deletion_status") == "frozen"
        ):
            raise TokenInvalidError("Ticket session no longer valid")

        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=srow["user_id"],
            user_id=srow["user_id"],
            session_id=srow["id"],
            username=srow["username"],
            permissions=permissions_from_json(srow["permissions"]),
            account_type=AccountType(srow["account_type"]),
            rate_limit_tier=srow.get("rate_limit_tier", "standard"),
            expires_at=srow["expires_at"],
            account_locked=bool(srow.get("account_locked", 0)),
            force_username_change=bool(srow.get("force_username_change", 0)),
            deletion_status=srow.get("deletion_status", "active"),
        )

    def refresh_session(self, token: str) -> Optional[str]:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "session":
            return None
        row = self._db.fetch_one(
            "SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],)
        )
        # The session secret must be verified, not just the (enumerable) ID,
        # otherwise anyone who learns a session ID can extend it forever.
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            return None
        if row["expires_at"] < self._get_timestamp():
            return None
        now = self._get_timestamp()
        expire_hours = self._config.get("sessions", {}).get("expire_hours", 720)
        new_expires = now + (expire_hours * 3600 * 1000)
        # Token rotation (F3): mint a fresh secret so an intercepted token
        # is only valid until the next refresh. Old token is invalidated
        # immediately by replacing token_hash; client storage must be updated
        # atomically (caller sets both bearer and cookie to the new token).
        new_token, new_hash = create_session_token(parsed["id"])
        self._db.execute(
            "UPDATE auth_sessions SET token_hash = ?, expires_at = ?, last_activity = ? WHERE id = ?",
            (new_hash, new_expires, now, parsed["id"]),
        )
        invalidate_pattern("token_verify:*")
        return new_token

    def logout(self, token: str) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            row = self._db.fetch_one(
                "SELECT user_id FROM auth_sessions WHERE id = ?", (parsed["id"],)
            )
            if row:
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
                )
                # Invalidate after the write so a concurrent verify_token cannot
                # re-cache the still-valid row in between.
                invalidate_pattern("token_verify:*")
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
        cursor = self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        # Invalidate after the write so a concurrent verify_token cannot
        # re-cache the still-valid row in between.
        invalidate_pattern("token_verify:*")
        if cursor.rowcount > 0:
            self._log_audit(AuditEventType.SESSION_REVOKED, user_id, True)
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "session_revoked")
            except Exception:
                pass
            return True
        return False
