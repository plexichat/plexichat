import hmac
import json
import time
from typing import Optional, List

import utils.logger as logger

from ..exceptions import (
    AuthError,
    UserNotFoundError,
    TokenInvalidError,
    TokenExpiredError,
    TwoFactorInvalidError,
    InvalidCredentialsError,
)
from ..models import (
    User,
    AccountType,
    AuditEventType,
    AuthResult,
    AuthStatus,
    TwoFactorSetup,
    TwoFactorChallenge,
    TwoFactorStatus,
)
from ..permissions import permissions_from_json
from ..tokens import create_2fa_challenge_token, parse_token, verify_token_hash
from .. import totp as totp_module
from src.core.database import invalidate_pattern


from .protocol import AuthManagerProtocol


class TwoFactorMixin(AuthManagerProtocol):
    # In-process sliding-window fallback used only when no Valkey backend is
    # configured (single-worker deployments and tests).
    _2fa_verify_attempts_local: dict = {}

    def _check_and_record_2fa_attempt(
        self, user_id: int, max_attempts: int = 5, window_seconds: int = 300
    ) -> None:
        """Rate-limit TOTP code guessing for non-challenge 2FA endpoints.

        Backed by the shared cache (Valkey primary) so the limit holds across
        all workers in a horizontally scaled deployment. SECURITY: this limiter
        fails CLOSED — if the cache backend errors while it is supposed to be
        available, attempts are refused rather than waved through.
        """
        from src.core.database.valkey_client import (
            get_client,
            is_available,
            ValkeyOperationError,
        )

        client = get_client()
        if client and is_available():
            full_key = f"ratelimit:auth_2fa_attempts:{user_id}"
            try:
                current = client.incr(full_key)
                if current == 1:
                    client.expire(full_key, window_seconds)
                if current > max_attempts:
                    raise TwoFactorInvalidError("Too many attempts; try again later")
                return
            except ValkeyOperationError:
                logger.error(
                    "2FA attempt limiter: cache backend error — failing closed"
                )
                raise TwoFactorInvalidError("Too many attempts; try again later")

        # No cache backend configured: in-process sliding window.
        now = time.monotonic()
        attempts = TwoFactorMixin._2fa_verify_attempts_local.get(user_id, [])
        attempts = [t for t in attempts if now - t < window_seconds]
        if len(attempts) >= max_attempts:
            raise TwoFactorInvalidError("Too many attempts; try again later")
        attempts.append(now)
        TwoFactorMixin._2fa_verify_attempts_local[user_id] = attempts

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

        ua_index = self._ua_index(ua)
        ua_encrypted = self._encrypt_ua(ua, str(cid))

        self._db.execute(
            "INSERT INTO auth_2fa_challenges (id, user_id, token_hash, device_id, ip_index, ip_encrypted, ua_index, ua_encrypted, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua_index,
                ua_encrypted,
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

    def complete_2fa(
        self,
        challenge_token: str,
        code: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
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

        # F1: bind challenge to client context at mint time (blind indexes)
        # and enforce at completion. Reject step-up mismatch when the
        # requester's IP/UA does not match the stored blind indexes.
        # Only enforce when both stored and presented values are available
        # so that programmatic callers without network context (tests) are
        # not broken; a real HTTP path will present an IP.
        if ip_address is not None and row.get("ip_index"):
            try:
                presented_ip_index = self.crypto.blind_index(ip_address, "ip_address")
                if not hmac.compare_digest(
                    str(row["ip_index"]), str(presented_ip_index)
                ):
                    logger.warning("2FA challenge IP binding mismatch")
                    raise TokenInvalidError("Client context mismatch")
            except TokenInvalidError:
                raise
            except Exception:
                logger.warning("2FA IP binding check failed", exc_info=True)
                raise TokenInvalidError("Client context mismatch")
        if user_agent is not None and row.get("ua_index"):
            try:
                presented_ua_index = self._ua_index(user_agent)
                if presented_ua_index and not hmac.compare_digest(
                    str(row["ua_index"]), str(presented_ua_index)
                ):
                    logger.warning("2FA challenge UA binding mismatch")
                    raise TokenInvalidError("Client context mismatch")
            except TokenInvalidError:
                raise
            except Exception:
                logger.warning("2FA UA binding check failed", exc_info=True)
                raise TokenInvalidError("Client context mismatch")

        # F2: shared per-user limiter (mirrors confirm_2fa/disable_2fa) so
        # fresh challenge tokens cannot reset the guessing budget. Fail-closed.
        user_id_for_limit = row.get("user_id")
        if user_id_for_limit is not None:
            self._check_and_record_2fa_attempt(int(user_id_for_limit))

        max_attempts = int(self._config.get("totp", {}).get("max_attempts", 3))
        if row.get("attempts", 0) >= max_attempts:
            raise TwoFactorInvalidError("Too many attempts")

        user_row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE id = ?", (row["user_id"],)
        )
        if not user_row:
            raise UserNotFoundError("User not found")

        user_id = user_row["id"]
        secret = totp_module.decrypt_totp_secret(user_row["totp_secret_encrypted"])

        try:
            is_totp_valid = totp_module.verify_totp_code(secret, code, user_id=user_id)
        except TypeError:
            is_totp_valid = totp_module.verify_totp_code(secret, code)

        if not is_totp_valid:
            backup_hashes = (
                json.loads(user_row["backup_codes_hash"])
                if user_row["backup_codes_hash"]
                else []
            )
            valid, index = totp_module.verify_backup_code(code, backup_hashes)
            if not valid:
                self._db.execute(
                    "UPDATE auth_2fa_challenges SET attempts = attempts + 1 WHERE id = ?",
                    (row["id"],),
                )
                raise TwoFactorInvalidError("Invalid code")
            # Atomic compare-and-set consumption: two concurrent requests
            # cannot both burn the same backup code, because the update only
            # succeeds when the stored list still matches what we verified.
            remaining_hashes = backup_hashes[:index] + backup_hashes[index + 1 :]
            cursor = self._db.execute(
                "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ? AND backup_codes_hash = ?",
                (
                    json.dumps(remaining_hashes),
                    user_id,
                    user_row["backup_codes_hash"],
                ),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                # The same code was consumed concurrently; treat as a failure.
                self._db.execute(
                    "UPDATE auth_2fa_challenges SET attempts = attempts + 1 WHERE id = ?",
                    (row["id"],),
                )
                raise TwoFactorInvalidError("Invalid code")

        # Atomic single-use consumption: the conditional update prevents two
        # concurrent requests from both completing with the same code.
        cursor = self._db.execute(
            "UPDATE auth_2fa_challenges SET used = 1, attempts = 0 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise TokenInvalidError("Expired/Used")

        ip_address = None
        try:
            if row.get("ip_encrypted"):
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context=str(row["id"])
                )
        except Exception:
            ip_address = None

        user_agent = None
        try:
            if row.get("ua_encrypted"):
                user_agent = self.crypto.decrypt_data(
                    row["ua_encrypted"], context=str(row["id"])
                )
        except Exception:
            user_agent = None

        session = self._create_session(
            user_id, row["device_id"], ip_address, user_agent
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
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        self._check_and_record_2fa_attempt(user_id)
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
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "two_factor_enabled")
        except Exception as e:
            # Non-raising by design (gateway emission must not break the
            # 2FA flow), but surface at WARNING so a broken security-alert
            # path is visible at default log levels.
            logger.warning(f"Failed to emit 2FA-enabled security alert: {e}")
        invalidate_pattern("user_data:*")
        return True

    def disable_2fa(
        self,
        user_id: int,
        password: str,
        code: str,
        except_session_id: Optional[int] = None,
    ) -> bool:
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
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        self._check_and_record_2fa_attempt(user_id)
        try:
            ok = totp_module.verify_totp_code(
                secret, code, user_id=f"disable:{user_id}"
            )
        except TypeError:
            ok = totp_module.verify_totp_code(secret, code)
        if not ok:
            raise TwoFactorInvalidError("Invalid code")
        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_hash = NULL WHERE id = ?",
            (user_id,),
        )
        invalidate_pattern("user_data:*")
        # F8: emit audit log entry for 2FA disable; policy: revoke other
        # sessions or at least re-challenge. We emit audit and revoke all
        # other sessions so that previously trusted sessions cannot bypass
        # the now-disabled second factor without re-authentication.
        self._log_audit(AuditEventType.TWO_FACTOR_DISABLED, user_id, True)
        try:
            # Policy: revoke all other sessions so previously trusted sessions
            # cannot bypass the now-disabled second factor. Preserve the
            # current session if the caller supplied it (route passes
            # current_user.session_id).
            if except_session_id is not None:
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0 AND id != ?",
                    (user_id, except_session_id),
                )
            else:
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                    (user_id,),
                )
            invalidate_pattern("token_verify:*")
        except Exception as e:
            # Security-relevant: if revocation fails, previously trusted
            # sessions survive the 2FA disable. Stay non-raising (the
            # disable itself succeeded) but log at WARNING so the gap is
            # visible at default levels instead of hidden at DEBUG.
            logger.warning(
                f"Failed to revoke sessions on 2FA disable for user {user_id}: {e}",
                exc_info=True,
            )
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "two_factor_disabled")
        except Exception as e:
            # Same non-raising contract as the enabled path: log loudly.
            logger.warning(f"Failed to emit 2FA-disabled security alert: {e}")
        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
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
