from ..exceptions import (
    UserNotFoundError,
    InvalidCredentialsError,
    WeakPasswordError,
    TokenInvalidError,
)
from ..models import AuditEventType, PasswordValidation
from ..passwords import validate_password as validate_pwd
from ..tokens import create_email_token, parse_token, verify_token_hash
from src.core.database import invalidate_pattern


class PasswordMixin:
    def change_password(self, user_id: int, old: str, new: str) -> bool:
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT password_hash FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not self.crypto.verify_password(old, user["password_hash"]):  # pyright: ignore[reportAttributeAccessIssue]
            raise InvalidCredentialsError("Invalid password")
        pwd_val = validate_pwd(new)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new), user_id),  # pyright: ignore[reportAttributeAccessIssue]
        )
        self._log_audit(AuditEventType.PASSWORD_CHANGE, user_id, True)  # pyright: ignore[reportAttributeAccessIssue]
        invalidate_pattern("user_data:*")
        return True

    def request_password_reset(self, email: str) -> bool:
        email_index = self.crypto.blind_index(email, "user_email")  # pyright: ignore[reportAttributeAccessIssue]
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
        )
        if not self.email_sender:  # pyright: ignore[reportAttributeAccessIssue]
            return False
        if not user:
            return True
        tid = self._generate_id()  # pyright: ignore[reportAttributeAccessIssue]
        token, token_hash = create_email_token(tid)
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "INSERT INTO auth_email_tokens (id, user_id, token_hash, token_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid,
                user["id"],
                token_hash,
                "reset_password",
                self._get_timestamp(),  # pyright: ignore[reportAttributeAccessIssue]
                self._get_timestamp() + 3600,  # pyright: ignore[reportAttributeAccessIssue]
            ),
        )
        self.email_sender.send(email, "Reset Password", f"Token: {token}")  # pyright: ignore[reportAttributeAccessIssue]
        return True

    def reset_password(self, token: str, new_password: str) -> bool:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "email":
            raise TokenInvalidError("Invalid token")
        rec = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_email_tokens WHERE id = ?", (parsed["id"],)
        )
        if (
            not rec
            or rec["used"]
            or rec["expires_at"] < self._get_timestamp()  # pyright: ignore[reportAttributeAccessIssue]
            or rec["token_type"] != "reset_password"
        ):
            raise TokenInvalidError("Invalid token")
        if not verify_token_hash(parsed["secret"], rec["token_hash"]):
            raise TokenInvalidError("Invalid token")
        pwd_val = validate_pwd(new_password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new_password), rec["user_id"]),  # pyright: ignore[reportAttributeAccessIssue]
        )
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ?", (rec["id"],)
        )
        invalidate_pattern(f"user_data:{rec['user_id']}*")
        invalidate_pattern(f"user_api:{rec['user_id']}*")
        return True

    def validate_password(self, password: str) -> PasswordValidation:
        return validate_pwd(password)
