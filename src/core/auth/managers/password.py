from ..exceptions import (
    UserNotFoundError,
    InvalidCredentialsError,
    WeakPasswordError,
    TokenInvalidError,
)
from ..models import AuditEventType, PasswordValidation
from ..passwords import validate_password as validate_pwd
from ..tokens import create_email_token, parse_token, verify_token_hash, is_expired
from src.core.database import invalidate_pattern

from ..gateway_revocation import close_user_gateway_connections

import utils.logger as logger

from .protocol import AuthManagerProtocol


class PasswordMixin(AuthManagerProtocol):
    def _invalidate_trusted_devices(self, user_id: int) -> None:
        """Forget every remembered device for this user.

        A device marked trusted skips the 2FA challenge at login with only the
        password, so it is a persistent second-factor bypass. A password
        change/reset is a credential transition: any previously trusted device
        is stale (it may be lost, stolen, or in an attacker's hands) and must
        not keep bypassing 2FA after all sessions have been revoked. Ensure the
        trust columns exist first so an old database fails closed here too.
        """
        self._ensure_trusted_device_columns()
        self._db.execute(
            "UPDATE auth_devices SET trusted = 0, trusted_at = NULL WHERE user_id = ?",
            (user_id,),
        )

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
        stored = row["password_hash"]
        if not bool(self.crypto.verify_password(password, stored)):
            return False
        # Transparent upgrade: legacy or stale-pepper hash -> rehash with the
        # current pepper/params now that the plaintext is proven correct.
        try:
            if self.crypto.needs_rehash(stored):
                self._db.execute(
                    "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                    (self.crypto.hash_password(password), user_id),
                )
        except Exception as exc:
            # The password was proven correct, so verification still succeeds;
            # the missed upgrade must not disappear silently.
            logger.debug(f"opportunistic rehash after verify failed: {exc}")
        return True

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
        # Revoking sessions alone is not enough: a trusted device keeps
        # skipping 2FA on the next login with the (new) password. Retire all
        # remembered devices so the credential change actually re-secures the
        # account.
        self._invalidate_trusted_devices(user_id)
        # SECURITY: an intentional password change is a credential reset. Any
        # password-reset link still outstanding (e.g. one an attacker induced
        # the victim to request) must die with the old password, otherwise that
        # stale link can be redeemed afterwards to overwrite the new one.
        self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 "
            "WHERE user_id = ? AND token_type = 'reset_password' AND used = 0",
            (user_id,),
        )
        invalidate_pattern("token_verify:*")
        self._log_audit(AuditEventType.PASSWORD_CHANGE, user_id, True)
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "password_change")
        except Exception as exc:
            # The password has already changed; a missing alert must not fail
            # the request, so record why instead of swallowing it.
            logger.debug(f"password_change alert not emitted: {exc}")
        # Password change revokes every session: close the live gateway sockets
        # too. The alert above is a client-side hint; until the socket is closed
        # it stays authenticated server-side.
        close_user_gateway_connections(user_id, "Password changed")
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
        # SECURITY: keep exactly one live password-reset token per user, the
        # same invariant the verification-email flow enforces. Without this,
        # every reset request leaves an independently valid link behind, so an
        # older (stolen/intercepted) link can still take over the account after
        # the victim redeemed a newer one. Re-issuing a reset supersedes all
        # earlier ones. Different token types are deliberately untouched.
        self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 "
            "WHERE user_id = ? AND token_type = 'reset_password' AND used = 0",
            (user["id"],),
        )
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
        except Exception as exc:
            # A malformed reset_password block falls back to the defaults below.
            logger.debug(f"reset_password config unusable, using defaults: {exc}")
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
        now = self._get_timestamp()
        # Fail closed on a corrupt/units-mismatched expiry (NULL, seconds
        # instead of ms, non-numeric) instead of comparing it as "not expired"
        # and granting a permanent reset credential.
        if (
            not rec
            or rec["used"]
            or is_expired(rec["expires_at"], now)
            or rec["token_type"] != "reset_password"
        ):
            raise TokenInvalidError("Invalid token")
        if not verify_token_hash(parsed["secret"], rec["token_hash"]):
            raise TokenInvalidError("Invalid token")
        pwd_val = validate_pwd(new_password)
        if not pwd_val.valid:
            raise WeakPasswordError(f"Weak: {pwd_val.issues}", pwd_val.issues)
        # Claim the single-use token BEFORE applying any credential change. The
        # conditional UPDATE is what enforces "used exactly once" under
        # concurrent replays; doing it first means a request that loses the
        # claim has not already written a new password hash.
        #
        # The SELECT above is only a fast-path validity check; every validity
        # predicate is re-checked in the atomic UPDATE so a token that expires
        # (or whose type/used state changes) between the read and the claim
        # cannot be redeemed. This mirrors the single-use guard used by email
        # verification and closes the time-of-check/time-of-use gap where an
        # almost-expired token's claim landed after its expiry.
        cursor = self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 "
            "WHERE id = ? AND used = 0 AND token_type = 'reset_password' "
            "AND expires_at > ?",
            (rec["id"], now),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise TokenInvalidError("Invalid token")
        # A successful reset is a credential change: retire every other
        # outstanding reset link for this user. The single-active-token
        # invariant is normally enforced when tokens are issued, but tokens
        # minted by older builds (or by a caller that bypasses the request
        # path) can still exist and must not survive a completed reset.
        self._db.execute(
            "UPDATE auth_email_tokens SET used = 1 "
            "WHERE user_id = ? AND token_type = 'reset_password' "
            "AND used = 0 AND id != ?",
            (rec["user_id"], rec["id"]),
        )
        self._db.execute(
            "UPDATE auth_users SET password_hash = ? WHERE id = ?",
            (self.crypto.hash_password(new_password), rec["user_id"]),
        )
        # Revoke all existing sessions so a compromised/reset credential cannot persist.
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (rec["user_id"],)
        )
        # A password reset must also retire remembered devices: otherwise a
        # stolen/lost device (or a leaked trusted fingerprint) still skips 2FA
        # with the newly reset password, defeating the recovery entirely.
        self._invalidate_trusted_devices(rec["user_id"])
        invalidate_pattern("token_verify:*")
        # A reset revokes every session: close live gateway sockets server-side
        # so the old credential cannot keep using an already-authenticated one.
        close_user_gateway_connections(rec["user_id"], "Password reset")
        invalidate_pattern(f"user_data:{rec['user_id']}*")
        invalidate_pattern(f"user_api:{rec['user_id']}*")
        return True

    def validate_password(self, password: str) -> PasswordValidation:
        return validate_pwd(password)
