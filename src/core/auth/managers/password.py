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


from .protocol import AuthManagerProtocol


class PasswordMixin(AuthManagerProtocol):
    def verify_user_password(self, user_id: int, password: str) -> bool:
        """Verify a user's password without creating a session.

        Unlike ``login()``, this performs no device/IP tracking, audit login
        events, or session issuance, so callers that only need to re-confirm
        the password do not leave orphaned sessions behind.
        """
        row = self._db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user_id,)
        )
        if not row or not row["password_hash"]:
            # Equalize timing with the found-user path.
            self.crypto.verify_password(password, self._dummy_password_hash)
            return False
        return bool(self.crypto.verify_password(password, row["password_hash"]))

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
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        invalidate_pattern("token_verify:*")
        self._log_audit(AuditEventType.PASSWORD_CHANGE, user_id, True)
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "password_change")
        except Exception:
            pass
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
            # Equalize timing with the found-user path to prevent account enumeration.
            self.crypto.verify_password("x", self._dummy_password_hash)
            return True
        # Per-email throttle: cap reset emails so a single address cannot be
        # mail-bombed by repeatedly hitting the endpoint.
        recent = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auth_email_tokens WHERE user_id = ? AND token_type = 'reset_password' AND created_at > ?",
            (user["id"], self._get_timestamp() - 3600 * 1000),
        )
        if recent and recent["cnt"] >= 3:
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
                # Timestamps are milliseconds (see ``_get_timestamp``); a bare
                # ``+ 3600`` here would expire tokens ~3.6 seconds after issue.
                self._get_timestamp() + 3600 * 1000,
            ),
        )
        # L8: deliver a deep link (the client pre-fills ?token=) instead of a
        # bare token that users would otherwise paste into a form - pasted
        # URLs/tokens linger in history and referrers. The token stays in the
        # email as a fallback for clients that cannot open links.
        import utils.config as config_util

        reset_cfg = {}
        try:
            reset_cfg = (config_util.get("authentication", {}) or {}).get(
                "reset_password", {}
            ) or {}
        except Exception:
            pass
        reset_path = str(reset_cfg.get("path") or "/reset-password").lstrip("/")
        client_url = str(reset_cfg.get("client_url") or "").rstrip("/")
        reset_url = (
            f"{client_url}/{reset_path}?token={token}"
            if client_url
            else f"/{reset_path}?token={token}"
        )
        self.email_sender.send(
            email,
            "Reset Password",
            f"Reset your password: {reset_url}\n\nToken (fallback): {token}",
        )
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
        # Atomic single-use consumption prevents concurrent requests from
        # both resetting the password with the same token.
        cursor = self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 WHERE id = ? AND used = 0",
            (rec["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise TokenInvalidError("Invalid token")
        # Revoke all existing sessions so a compromised/reset credential cannot persist.
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (rec["user_id"],)
        )
        invalidate_pattern("token_verify:*")
        invalidate_pattern(f"user_data:{rec['user_id']}*")
        invalidate_pattern(f"user_api:{rec['user_id']}*")
        return True

    def validate_password(self, password: str) -> PasswordValidation:
        return validate_pwd(password)
