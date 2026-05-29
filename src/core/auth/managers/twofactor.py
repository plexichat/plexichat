import json
from typing import Optional, List

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


class TwoFactorMixin:
    def _create_2fa_challenge(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> TwoFactorChallenge:
        cid = self._generate_id()  # pyright: ignore[reportAttributeAccessIssue]
        now = self._get_timestamp()  # pyright: ignore[reportAttributeAccessIssue]
        expires = now + 300000
        token, token_hash = create_2fa_challenge_token(cid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(cid))  # pyright: ignore[reportAttributeAccessIssue]

        ua_index = self._ua_index(ua)  # pyright: ignore[reportAttributeAccessIssue]
        ua_encrypted = self._encrypt_ua(ua, str(cid))  # pyright: ignore[reportAttributeAccessIssue]

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
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

    def complete_2fa(self, challenge_token: str, code: str) -> AuthResult:
        parsed = parse_token(challenge_token)
        if not parsed or parsed["token_type"] != "2fa":
            raise TokenInvalidError("Invalid challenge")
        row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_2fa_challenges WHERE id = ?", (parsed["id"],)
        )
        if not row:
            raise TokenInvalidError("Expired/Used")
        if row["used"]:
            raise TokenInvalidError("Expired/Used")
        stored_hash = row.get("challenge_hash") or row.get("token_hash")
        if not stored_hash or not verify_token_hash(parsed["secret"], stored_hash):
            raise TokenInvalidError("Expired/Used")
        if row["expires_at"] < self._get_timestamp():  # pyright: ignore[reportAttributeAccessIssue]
            raise TokenExpiredError("Expired")

        user_row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
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
                raise TwoFactorInvalidError("Invalid code")
            backup_hashes.pop(index)
            self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
                "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
                (json.dumps(backup_hashes), user_id),
            )

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_2fa_challenges SET used = 1 WHERE id = ?", (row["id"],)
        )

        ip_address = None
        try:
            if row.get("ip_encrypted"):
                ip_address = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                    row["ip_encrypted"], context=str(row["id"])
                )
        except Exception:
            ip_address = None

        user_agent = None
        try:
            if row.get("ua_encrypted"):
                user_agent = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                    row["ua_encrypted"], context=str(row["id"])
                )
        except Exception:
            user_agent = None

        session = self._create_session(  # pyright: ignore[reportAttributeAccessIssue]
            user_id, row["device_id"], ip_address, user_agent
        )
        email = (
            self.crypto.decrypt_data(user_row["email_encrypted"], context=str(user_id))  # pyright: ignore[reportAttributeAccessIssue]
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
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT username, totp_enabled FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if user["totp_enabled"]:
            raise AuthError("2FA already enabled")
        secret = totp_module.generate_totp_secret()
        backup_codes = totp_module.generate_backup_codes()
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
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
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT totp_secret_encrypted FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not initiated")
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        try:
            ok = totp_module.verify_totp_code(secret, code, user_id=f"setup:{user_id}")
        except TypeError:
            ok = totp_module.verify_totp_code(secret, code)
        if not ok:
            raise TwoFactorInvalidError("Invalid code")

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?", (user_id,)
        )
        self._log_audit(AuditEventType.TWO_FACTOR_ENABLED, user_id, True)  # pyright: ignore[reportAttributeAccessIssue]
        invalidate_pattern("user_data:*")
        return True

    def disable_2fa(self, user_id: int, password: str, code: str) -> bool:
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT password_hash, totp_secret_encrypted, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled") or not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):  # pyright: ignore[reportAttributeAccessIssue]
            raise InvalidCredentialsError("Invalid password")
        secret = totp_module.decrypt_totp_secret(user["totp_secret_encrypted"])
        try:
            ok = totp_module.verify_totp_code(
                secret, code, user_id=f"disable:{user_id}"
            )
        except TypeError:
            ok = totp_module.verify_totp_code(secret, code)
        if not ok:
            raise TwoFactorInvalidError("Invalid code")
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_hash = NULL WHERE id = ?",
            (user_id,),
        )
        invalidate_pattern("user_data:*")
        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        user = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT password_hash, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):  # pyright: ignore[reportAttributeAccessIssue]
            raise InvalidCredentialsError("Invalid password")
        codes = totp_module.generate_backup_codes()
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
            (json.dumps(totp_module.hash_backup_codes(codes)), user_id),
        )
        return codes

    def get_2fa_status(self, user_id: int) -> TwoFactorStatus:
        row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT totp_enabled, backup_codes_hash FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")
        codes = json.loads(row["backup_codes_hash"]) if row["backup_codes_hash"] else []
        return TwoFactorStatus(
            enabled=bool(row["totp_enabled"]), backup_codes_remaining=len(codes)
        )
