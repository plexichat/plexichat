from typing import Optional, Dict

import sqlite3

import utils.logger as logger

from ..exceptions import (
    AuthError,
    InvalidUsernameError,
    InvalidEmailError,
    WeakPasswordError,
    UserExistsError,
)
from ..models import User, AccountType, AuditEventType
from ..permissions import DEFAULT_USER_PERMISSIONS, permissions_to_json
from ..passwords import (
    validate_password as validate_pwd,
    validate_username,
    validate_email,
)
from ..tokens import create_email_token, parse_token, verify_token_hash


from .protocol import AuthManagerProtocol


class RegistrationMixin(AuthManagerProtocol):
    def register(
        self,
        username: str,
        email: str,
        password: str,
        device_info: Optional[Dict[str, str]] = None,
        ip_address: Optional[str] = None,
        age: Optional[int] = None,
        dob: Optional[str] = None,
        is_internal: bool = False,
    ) -> User:
        user_id = self._generate_id()

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
                    stored_dob = self.crypto.encrypt_data(dob, context=str(user_id))
                except ValueError:
                    raise AuthError("Invalid date format. Use YYYY-MM-DD", "dob")
            else:
                if age is not None:
                    if age < min_age:
                        raise AuthError(
                            f"Minimum age requirement not met ({min_age})", "age"
                        )
                    age_verified = 1
                else:
                    raise AuthError("Age is required", "age")

        valid, issues = validate_username(username)
        if not valid:
            raise InvalidUsernameError(f"Invalid: {issues}", issues)

        if not is_internal:
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
            "SELECT id FROM auth_users WHERE LOWER(username) = LOWER(?)", (username,)
        ):
            raise UserExistsError("Registration failed", "username")

        email_index = self.crypto.blind_index(email, "user_email")
        if self._db.fetch_one(
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        ):
            raise UserExistsError("Registration failed", "email")

        # Enforce the license "max_users" limit (only when a finite cap is set).
        try:
            from utils.licensing import get_license_limit, is_limit_enforced

            _user_cap = get_license_limit("max_users", default=-1)
            if is_limit_enforced(_user_cap):
                _row = self._db.fetch_one("SELECT COUNT(*) AS c FROM auth_users")
                _count = int(_row["c"]) if _row else 0
                if _count >= _user_cap:
                    raise AuthError(
                        f"Registration limit reached: this instance permits a "
                        f"maximum of {_user_cap} users.",
                        "user_limit",
                    )
        except (RuntimeError, ImportError):
            # Licensing is not configured; do not enforce.
            pass

        now = self._get_timestamp()
        email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))
        password_hash = self.crypto.hash_password(password)
        require_ver = accounts_config.get("require_email_verification", False)

        # M4: the SELECT checks above are a fast path only - two concurrent
        # registrations can both pass them. The DB constraints (UNIQUE on
        # username / email index) are the real backstop; surface the race as
        # a 409-style UserExistsError instead of an unhandled IntegrityError
        # that bubbles up as a 500.
        try:
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
        except Exception as e:
            msg = str(e).upper()
            unique_markers = (
                "UNIQUE",
                "DUPLICATE KEY",
                "UNIQUE_VIOLATION",
                "23505",
            )
            if isinstance(e, sqlite3.IntegrityError) or any(
                marker in msg for marker in unique_markers
            ):
                raise UserExistsError("Registration failed", "username_or_email") from e
            raise

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

    @staticmethod
    def _token_field(rec, name: str, pos: int):
        # fetch_one() normalizes to dict, but tolerate tuple/sqlite3.Row
        # shapes so a driver change cannot turn into a KeyError/500.
        if isinstance(rec, dict):
            return rec.get(name)
        try:
            return rec[name]  # type: ignore[index]
        except Exception:
            pass
        try:
            return rec[pos]  # type: ignore[index]
        except Exception:
            return None

    def verify_email(self, token: str) -> bool:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            return False
        rec = self._db.fetch_one(
            "SELECT * FROM auth_email_tokens WHERE id = ?", (parsed["id"],)
        )
        if not rec:
            return False
        # Column order of SELECT *: id(0), user_id(1), token_hash(2),
        # token_type(3), expires_at(4), used(5), created_at(6).
        used = self._token_field(rec, "used", 5)
        expires_at = self._token_field(rec, "expires_at", 4)
        token_type = self._token_field(rec, "token_type", 3)
        token_hash = self._token_field(rec, "token_hash", 2)
        rec_id = self._token_field(rec, "id", 0)
        user_id = self._token_field(rec, "user_id", 1)
        now = self._get_timestamp()
        if used or expires_at is None or expires_at < now:
            return False
        if token_type != "verify_email":
            return False
        if not verify_token_hash(parsed["secret"], token_hash or ""):
            return False

        # M3: atomic single-use consumption. The SELECT above is only a
        # fast-path validity check; the conditional UPDATE is authoritative.
        # Concurrent replays both pass the SELECT but only one UPDATE finds
        # an unconsumed, unexpired row (rowcount 1). All other outcomes
        # (already-used/invalid/expired/type-changed) yield rowcount 0 and
        # fail closed without marking the user verified. The expiry bound
        # is re-checked in the UPDATE so a token expiring between SELECT
        # and UPDATE cannot be consumed.
        cursor = self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 "
            "WHERE id = ? AND used = 0 AND token_type = 'verify_email' "
            "AND expires_at >= ?",
            (rec_id, now),
        )
        try:
            rowcount = getattr(cursor, "rowcount", 0)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        if rowcount != 1:
            return False
        self._db.execute(
            "UPDATE auth_users SET email_verified = 1 WHERE id = ?", (user_id,)
        )
        self._log_audit(AuditEventType.EMAIL_VERIFIED, user_id, True)
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
        # ``_get_timestamp()`` is MILLISECONDS; expires_at must therefore be
        # now + 24h * 3600 s/h * 1000 ms/s. (86-seconds bug when the day was
        # encoded as 86400 raw.)
        now = self._get_timestamp()
        self._db.execute(
            "INSERT INTO auth_email_tokens (id, user_id, token_hash, token_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, user_id, token_hash, "verify_email", now, now + 86400 * 1000),
        )
        try:
            self.email_sender.send(
                email, "Verify Email", f"Verification Token: {token}"
            )
            return True
        except Exception as e:
            logger.error(f"Email failed: {e}")
            return False
