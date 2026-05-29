from typing import Optional, List, Dict, Any

from ..models import AuditEventType
from ..exceptions import UserNotFoundError, InvalidCredentialsError, AccountLockedError
from ..models import AuthStatus, User, AccountType, AuthResult
from ..permissions import permissions_from_json


from .protocol import AuthManagerProtocol


class PasskeyMixin(AuthManagerProtocol):
    def generate_passkey_registration_options(
        self,
        user_id: int,
        device_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.passkeys.is_available():
            raise RuntimeError("Passkey support not available")

        user = self.get_user(user_id)
        if not user:
            raise UserNotFoundError("User not found")

        options = self.passkeys.generate_registration_options(
            user_id=user_id,
            username=user.username,
            device_name=device_name,
        )

        if not options:
            return None

        return {
            "challenge_id": options.challenge_id,
            "options": options.options_dict,
        }

    def verify_passkey_registration(
        self,
        user_id: int,
        challenge_id: str,
        credential_response: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.passkeys.is_available():
            raise RuntimeError("Passkey support not available")

        credential = self.passkeys.verify_registration(
            user_id=user_id,
            challenge_id=challenge_id,
            credential_response=credential_response,
        )

        if not credential:
            return None

        self._log_audit(
            AuditEventType.PASSKEY_REGISTERED,
            user_id,
            True,
            ip_address,
            details={"credential_id": credential.credential_id[:20] + "..."},
        )

        return {
            "id": credential.id,
            "credential_id": credential.credential_id,
            "device_name": credential.device_name,
            "device_type": credential.device_type,
            "backed_up": credential.backed_up,
        }

    def generate_passkey_authentication_options(
        self,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.passkeys.is_available():
            raise RuntimeError("Passkey support not available")

        options = self.passkeys.generate_authentication_options(username=username)

        return {
            "challenge_id": options.challenge_id,
            "options": options.options_dict,
        }

    def verify_passkey_authentication(
        self,
        challenge_id: str,
        credential_response: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        if not self.passkeys.is_available():
            raise RuntimeError("Passkey support not available")

        result = self.passkeys.verify_authentication(
            challenge_id=challenge_id,
            credential_response=credential_response,
        )

        if not result:
            raise InvalidCredentialsError("Passkey authentication failed")

        user_id = result["user_id"]

        user_row = self._db.fetch_one(
            """SELECT * FROM auth_users WHERE id = ?""",
            (user_id,),
        )
        if not user_row:
            raise UserNotFoundError("User not found")

        if user_row["account_locked"]:
            raise AccountLockedError("Account locked")

        device_id = (
            self._track_device(user_id, {"type": "passkey"}) if user_id else None
        )
        if ip_address:
            self._track_ip(user_id, ip_address)

        session = self._create_session(user_id, device_id, ip_address, user_agent)

        self._db.execute(
            "UPDATE auth_users SET last_login_at = ? WHERE id = ?",
            (self._get_timestamp(), user_id),
        )

        self._log_audit(
            AuditEventType.PASSKEY_AUTHENTICATED,
            user_id,
            True,
            ip_address,
            device_id,
            details={"credential_id": result["credential_id"][:20] + "..."},
        )

        email = None
        if user_row["email_encrypted"]:
            try:
                email = self.crypto.decrypt_data(
                    user_row["email_encrypted"], context=str(user_id)
                )
            except Exception:
                email = "[decryption failed]"

        user = User(
            id=user_id,
            account_type=AccountType(user_row["account_type"]),
            username=user_row["username"],
            email=email,
            permissions=permissions_from_json(user_row["permissions"]),
            created_at=user_row["created_at"],
            updated_at=user_row["updated_at"],
            email_verified=bool(user_row.get("email_verified", 0)),
            account_locked=bool(user_row.get("account_locked", 0)),
            force_username_change=bool(user_row.get("force_username_change", 0)),
            failed_login_attempts=user_row.get("failed_login_attempts", 0),
            locked_until=user_row.get("locked_until"),
            last_login_at=self._get_timestamp(),
            totp_enabled=bool(user_row.get("totp_enabled", 0)),
            age_verified=bool(user_row.get("age_verified", 0)),
        )

        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user,
            session=session,
        )

    def list_passkeys(self, user_id: int) -> List[Dict[str, Any]]:
        if not self.passkeys.is_available():
            return []

        passkeys = self.passkeys.list_passkeys(user_id)
        return [
            {
                "id": p.id,
                "credential_id": p.credential_id,
                "device_name": p.device_name,
                "device_type": p.device_type,
                "created_at": p.created_at,
                "last_used_at": p.last_used_at,
                "backed_up": p.backed_up,
                "revoked": p.revoked,
            }
            for p in passkeys
        ]

    def revoke_passkey(
        self, user_id: int, passkey_id: int, ip_address: Optional[str] = None
    ) -> bool:
        if not self.passkeys.is_available():
            return False

        result = self.passkeys.revoke_passkey(user_id, passkey_id)

        if result:
            self._log_audit(
                AuditEventType.PASSKEY_REVOKED,
                user_id,
                True,
                ip_address,
                details={"passkey_id": passkey_id},
            )

        return result

    def rename_passkey(self, user_id: int, passkey_id: int, new_name: str) -> bool:
        if not self.passkeys.is_available():
            return False

        result = self.passkeys.rename_passkey(user_id, passkey_id, new_name)

        if result:
            self._log_audit(
                AuditEventType.PASSKEY_RENAMED,
                user_id,
                True,
                details={"passkey_id": passkey_id, "new_name": new_name},
            )

        return result
