from typing import Optional, Dict

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


class RegistrationMixin:
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
        user_id = self._generate_id()  # pyright: ignore[reportAttributeAccessIssue]

        accounts_config = self._config.get("accounts", {})  # pyright: ignore[reportAttributeAccessIssue]
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
                    stored_dob = self.crypto.encrypt_data(dob, context=str(user_id))  # pyright: ignore[reportAttributeAccessIssue]
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
            blocked, reason = self.blacklist.is_blocked(username)  # pyright: ignore[reportAttributeAccessIssue]
            if blocked:
                raise InvalidUsernameError(
                    f"Username is blocked: {reason}", [reason or "Blocked"]
                )

        if not validate_email(email):
            raise InvalidEmailError("Invalid email")

        pwd_val = validate_pwd(password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)

        if self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        ):
            raise UserExistsError("Registration failed", "username")

        email_index = self.crypto.blind_index(email, "user_email")  # pyright: ignore[reportAttributeAccessIssue]
        if self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        ):
            raise UserExistsError("Registration failed", "email")

        now = self._get_timestamp()  # pyright: ignore[reportAttributeAccessIssue]
        email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))  # pyright: ignore[reportAttributeAccessIssue]
        password_hash = self.crypto.hash_password(password)  # pyright: ignore[reportAttributeAccessIssue]
        require_ver = accounts_config.get("require_email_verification", False)

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
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
            self._track_ip(user_id, ip_address)  # pyright: ignore[reportAttributeAccessIssue]
        if device_info:
            self._track_device(user_id, device_info)  # pyright: ignore[reportAttributeAccessIssue]
        self._log_audit(AuditEventType.REGISTER, user_id, True, ip_address)  # pyright: ignore[reportAttributeAccessIssue]

        if require_ver and self.email_sender:  # pyright: ignore[reportAttributeAccessIssue]
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
        rec = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_email_tokens WHERE id = ?", (parsed["id"],)
        )
        if not rec or rec["used"] or rec["expires_at"] < self._get_timestamp():  # pyright: ignore[reportAttributeAccessIssue]
            return False
        if rec["token_type"] != "verify_email":
            return False
        if not verify_token_hash(parsed["secret"], rec["token_hash"]):
            return False

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?", (rec["id"],)
        )
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET email_verified = 1 WHERE id = ?", (rec["user_id"],)
        )
        self._log_audit(AuditEventType.EMAIL_VERIFIED, rec["user_id"], True)  # pyright: ignore[reportAttributeAccessIssue]
        return True

    def resend_verification(self, email: str) -> bool:
        email_index = self.crypto.blind_index(email, "user_email")  # pyright: ignore[reportAttributeAccessIssue]
        row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT id, email_verified FROM auth_users WHERE email_index = ?",
            (email_index,),
        )
        if not row or row["email_verified"]:
            return True
        return self._send_verification_email(row["id"], email)

    def _send_verification_email(self, user_id: int, email: str) -> bool:
        if not self.email_sender:  # pyright: ignore[reportAttributeAccessIssue]
            return False
        tid = self._generate_id()  # pyright: ignore[reportAttributeAccessIssue]
        token, token_hash = create_email_token(tid)
        now = self._get_timestamp()  # pyright: ignore[reportAttributeAccessIssue]
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "INSERT INTO auth_email_tokens (id, user_id, token_hash, token_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, user_id, token_hash, "verify_email", now, now + 86400),
        )
        try:
            self.email_sender.send(  # pyright: ignore[reportAttributeAccessIssue]
                email, "Verify Email", f"Verification Token: {token}"
            )
            return True
        except Exception as e:
            logger.error(f"Email failed: {e}")
            return False
