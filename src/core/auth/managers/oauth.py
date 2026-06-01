from typing import Optional

import secrets

import utils.logger as logger

from ..exceptions import (
    PermissionDeniedError,
    AuthError,
)
from ..models import (
    TokenInfo,
    AuthResult,
    AuditEventType,
    AuthStatus,
)
from ..permissions import has_permission


from .protocol import AuthManagerProtocol


class OAuthMixin(AuthManagerProtocol):
    def has_capability(self, token_info: TokenInfo, capability: str) -> bool:
        return has_permission(token_info.permissions, capability)

    def require_capability(self, token_info: TokenInfo, capability: str) -> None:
        if not self.has_capability(token_info, capability):
            raise PermissionDeniedError(f"Missing required permission: {capability}")

    def _encrypt_external_id(self, external_id: str) -> Optional[str]:
        try:
            return self.crypto.encrypt_data(
                external_id, context="external_account:oauth"
            )
        except Exception as e:
            logger.warning(f"Failed to encrypt external_id: {e}")
            return None

    def oauth_login(
        self,
        provider: str,
        external_id: str,
        email: Optional[str] = None,
        username_hint: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        age: Optional[int] = None,
        dob: Optional[str] = None,
    ) -> AuthResult:
        row = self._db.fetch_one(
            "SELECT user_id FROM auth_external_accounts WHERE provider = ? AND external_id = ?",
            (provider, external_id),
        )

        user_id = None
        if row:
            user_id = row["user_id"]
        else:
            if email:
                email_index = self.crypto.blind_index(email, "user_email")
                user_row = self._db.fetch_one(
                    "SELECT id FROM auth_users WHERE email_index = ?", (email_index,)
                )
                if user_row:
                    user_id = user_row["id"]
                    external_id_encrypted = self._encrypt_external_id(external_id)
                    self._db.insert_or_ignore(
                        "auth_external_accounts",
                        [
                            "id",
                            "user_id",
                            "provider",
                            "external_id",
                            "external_id_encrypted",
                            "email_index",
                            "created_at",
                        ],
                        (
                            self._generate_id(),
                            user_id,
                            provider,
                            external_id,
                            external_id_encrypted,
                            email_index,
                            self._get_timestamp(),
                        ),
                    )

        if not user_id:
            accounts_config = self._config.get("accounts", {})
            if accounts_config.get("age_gate_enabled", False):
                if age is None and dob is None:
                    return AuthResult(
                        status=AuthStatus.FAILED, message="Age verification required"
                    )

            base_username = username_hint or (
                email.split("@")[0] if email else f"{provider}_{external_id[:8]}"
            )

            username = base_username
            attempts = 0
            while attempts < 10 and self._db.fetch_one(
                "SELECT id FROM auth_users WHERE username = ?", (username,)
            ):
                username = f"{base_username}{secrets.token_hex(2)}"
                attempts += 1

            random_password = secrets.token_urlsafe(32)

            try:
                user = self.register(
                    username=username,
                    email=email or f"{external_id}@{provider}.internal",
                    password=random_password,
                    ip_address=ip_address,
                    age=age,
                    dob=dob,
                )
                user_id = user.id

                email_index = self.crypto.blind_index(
                    email or f"{external_id}@{provider}.internal", "user_email"
                )
                external_id_encrypted = self._encrypt_external_id(external_id)
                self._db.insert_or_ignore(
                    "auth_external_accounts",
                    [
                        "id",
                        "user_id",
                        "provider",
                        "external_id",
                        "external_id_encrypted",
                        "email_index",
                        "created_at",
                    ],
                    (
                        self._generate_id(),
                        user_id,
                        provider,
                        external_id,
                        external_id_encrypted,
                        email_index,
                        self._get_timestamp(),
                    ),
                )
            except AuthError as e:
                self._log_audit(
                    AuditEventType.LOGIN_FAILED,
                    None,
                    False,
                    ip_address,
                    details={"error": str(e), "provider": provider},
                )
                return AuthResult(status=AuthStatus.FAILED, message=str(e))

        user_obj = self.get_user(user_id)
        if not user_obj:
            return AuthResult(status=AuthStatus.FAILED, message="User creation failed")

        if user_obj.account_locked:
            return AuthResult(status=AuthStatus.FAILED, message="Account locked")

        session = self._create_session(user_id, None, ip_address, user_agent)

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE auth_users SET last_login_at = ?, failed_login_attempts = 0 WHERE id = ?",
            (now, user_id),
        )
        self._db.execute(
            "UPDATE auth_external_accounts SET last_login_at = ? WHERE user_id = ? AND provider = ?",
            (now, user_id, provider),
        )

        self._log_audit(
            AuditEventType.LOGIN_SUCCESS,
            user_id,
            True,
            ip_address,
            details={"provider": provider},
        )

        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user_obj,
            session=session,
        )
