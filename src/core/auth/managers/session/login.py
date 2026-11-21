"""Login, session creation, throttling and failed-login bookkeeping."""

import datetime
import ipaddress
from typing import Dict, Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ...exceptions import (
    AccountLockedError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    UserNotFoundError,
)
from ...models import AccountType, AuditEventType, AuthResult, AuthStatus, User
from ...permissions import permissions_from_json
from ..protocol import AuthManagerProtocol


# Sentinel user id for the *global*, per-source-IP failure budget stored in
# auth_failed_logins. Real snowflake ids start at 1, so -1 can never collide
# with an actual account, and the existing per-(user, IP) rows/queries that
# filter on a real user id are unaffected by these rows.
_GLOBAL_IP_SENTINEL_USER_ID = -1
# Default number of failed logins, from ANY account, allowed from one source
# before that source is uniformly refused. Mirrors the HTTP-layer login route
# cap but lives in the manager so it still holds for non-HTTP callers and when
# the application rate-limit middleware is disabled.
DEFAULT_CREDENTIAL_STUFFING_IP_LIMIT = 50


def _canonical_source_ip(ip_address: Optional[str]) -> Optional[str]:
    """Return one stable textual form for a source address.

    ``blind_index`` hashes the raw string (after lower/strip), so equivalent
    IPv6 spellings of a single address would each get a separate failure row.
    A client behind a trusted proxy controls the X-Forwarded-For spelling, so
    that let one source rotate spellings to reset both its per-user and global
    failure budgets, and to inflate ``COUNT(DISTINCT ip_index)`` past
    ``lockout_min_distinct_ips`` and lock an arbitrary account from one host.
    Canonicalising here makes every spelling share one budget. Unparseable
    values are passed through so they remain attributable (and rate-limited)
    rather than being silently dropped.
    """
    if not ip_address:
        return None
    text = ip_address.strip()
    try:
        parsed = ipaddress.ip_address(text)
        # An IPv4 peer may surface as ::ffff:a.b.c.d from a dual-stack socket
        # but as a.b.c.d in an X-Forwarded-For header. Fold the mapped form so
        # both spellings of the same peer share a failure budget too.
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            return str(parsed.ipv4_mapped)
        canonical = str(parsed)
        # An IPv6 zone/scope id names a local interface, not a distinct host.
        # Keeping it would let one source rotate the failure-budget key (or
        # inflate COUNT(DISTINCT ip_index) to lock out an arbitrary account),
        # so strip it just like the rate-limit principal canonicalizer does.
        if isinstance(parsed, ipaddress.IPv6Address) and "%" in canonical:
            canonical = canonical.split("%", 1)[0]
        return canonical
    except ValueError:
        return text


class LoginMixin(AuthManagerProtocol):
    def _failure_ip_index(self, ip_address: Optional[str]) -> Optional[str]:
        """Blind index of the canonical source IP for failure bookkeeping.

        All per-IP/per-(user, IP) rows in ``auth_failed_logins`` must use this
        helper so throttling, lockout and the global credential-stuffing budget
        agree on what constitutes one source.
        """
        canonical = _canonical_source_ip(ip_address)
        return self.crypto.blind_index(canonical, "ip_address") if canonical else None

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
        # Global per-source credential-stuffing budget. The per-(user, IP)
        # throttle and the distributed account lockout below are both keyed to
        # an individual account, so an attacker spraying ONE password across
        # thousands of accounts never trips either: each account sees a single
        # failure from a single IP. This budget is keyed to the source IP only,
        # so enough failures against *any* accounts from one source make every
        # subsequent attempt from that source fail uniformly. It is checked
        # before the account lookup so a throttled source cannot enumerate
        # usernames either. Does not touch account state (no lockout DoS).
        ip_index_for_budget = self._failure_ip_index(ip_address)
        if ip_index_for_budget:
            self._ensure_failed_logins_table()
            if self._global_ip_throttle_active(ip_index_for_budget):
                self.crypto.verify_password(password, self._dummy_password_hash)
                logger.warning(
                    "Login throttled: source IP exhausted credential-stuffing budget"
                )
                raise InvalidCredentialsError("Invalid credentials")

        email_index = self.crypto.blind_index(username, "user_email")
        row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE username = ? OR email_index = ?",
            (username, email_index),
        )
        if not row:
            # A nonexistent username is the common credential-stuffing case, so
            # it must consume the per-IP budget just like a real wrong password.
            if ip_address:
                self._record_global_ip_failure(ip_address)
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
            ip_index = self._failure_ip_index(ip_address)
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
        except Exception as exc:
            # Login proceeds with the verified stored hash; the skipped upgrade
            # is logged so a stale pepper does not go unnoticed.
            logger.debug(f"password rehash on login failed: {exc}")

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
        if LoginMixin._failed_logins_table_ready:
            return
        from ...schema import ensure_failed_logins_table

        ensure_failed_logins_table(self._db)
        LoginMixin._failed_logins_table_ready = True

    def _credential_stuffing_ip_limit(self) -> int:
        """Per-source-IP failure budget before uniform throttling kicks in."""
        security_config = self._config.get("security", {})
        try:
            limit = int(
                security_config.get(
                    "credential_stuffing_ip_limit",
                    DEFAULT_CREDENTIAL_STUFFING_IP_LIMIT,
                )
            )
        except (TypeError, ValueError):
            limit = DEFAULT_CREDENTIAL_STUFFING_IP_LIMIT
        return max(1, limit)

    def _global_ip_throttle_active(self, ip_index: str) -> bool:
        """Whether one source has exhausted the cross-account failure budget.

        Fail closed on unparseable counts/timestamps: a corrupt sentinel row
        must be treated as throttled rather than as an open abuse corridor.
        """
        row = self._db.fetch_one(
            "SELECT attempts, last_attempt_at FROM auth_failed_logins "
            "WHERE user_id = ? AND ip_index = ?",
            (_GLOBAL_IP_SENTINEL_USER_ID, ip_index),
        )
        if not row:
            return False
        security_config = self._config.get("security", {})
        try:
            window_minutes = max(
                1, int(security_config.get("failed_login_window_minutes", 15))
            )
        except (TypeError, ValueError):
            window_minutes = 15
        try:
            last = int(row.get("last_attempt_at", 0))
            attempts = int(row.get("attempts", 0))
        except (TypeError, ValueError):
            return True
        cutoff = self._get_timestamp() - window_minutes * 60 * 1000
        if last <= cutoff:
            return False
        return attempts >= self._credential_stuffing_ip_limit()

    def _record_global_ip_failure(self, ip_address: str) -> None:
        """Increment the cross-account failure budget for one source IP."""
        self._ensure_failed_logins_table()
        ip_index = self._failure_ip_index(ip_address)
        now = self._get_timestamp()
        try:
            self._db.execute(
                "INSERT INTO auth_failed_logins "
                "(user_id, ip_index, attempts, last_attempt_at) VALUES (?, ?, 1, ?)",
                (_GLOBAL_IP_SENTINEL_USER_ID, ip_index, now),
            )
        except Exception:
            # Concurrent insert for the same source IP; count it.
            self._db.execute(
                "UPDATE auth_failed_logins "
                "SET attempts = attempts + 1, last_attempt_at = ? "
                "WHERE user_id = ? AND ip_index = ?",
                (now, _GLOBAL_IP_SENTINEL_USER_ID, ip_index),
            )

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

        # A wrong password for an existing account also spends the caller's
        # cross-account failure budget (the nonexistent-username path records
        # it in login()). Together these close the credential-stuffing gap
        # where spraying many accounts from one source evades every per-account
        # control.
        if ip_address:
            self._record_global_ip_failure(ip_address)

        # Atomic increment: no read-modify-write race between concurrent
        # requests can lose or double-count a failure.
        self._db.execute(
            "UPDATE auth_users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )

        distinct_ips = 0
        now = self._get_timestamp()
        if ip_address:
            ip_index = self._failure_ip_index(ip_address)
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
            if now - LoginMixin._failed_logins_last_cleanup > 3600 * 1000:
                LoginMixin._failed_logins_last_cleanup = now
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
                # The lock flips account state that is embedded in the cached
                # TokenInfo (account_locked). The admin status-toggle path drops
                # these caches via invalidate_privilege_caches(); a lock reached
                # through failed logins must do the same, otherwise a live
                # session keeps answering "unlocked" until the verify TTL and
                # the auth middleware never suspends the account.
                from src.core.auth.privilege_cache import invalidate_privilege_caches

                invalidate_privilege_caches()
                try:
                    from src.core.events.gateway_emit import emit_security_alert

                    emit_security_alert(user_id, "account_locked")
                except Exception as exc:
                    logger.debug(f"account_locked alert not emitted: {exc}")

        invalidate_pattern("user_data:*")
