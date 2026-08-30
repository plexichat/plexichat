"""
AuthManager protocol class.

Declares all shared attributes and methods that auth mixins access via self
but are defined on sibling mixins or the parent AuthManager class.

Each mixin inherits from this class so pyright can resolve cross-mixin
attribute access without static suppression comments.
"""

from typing import Any, Optional, Dict

from ..models import User, Session, TokenInfo, TwoFactorChallenge


class AuthManagerProtocol:
    _db: Any = None
    crypto: Any = None
    _config: Dict[str, Any] = {}
    email_sender: Any = None

    def _send_verification_email(self, user_id: int, email: str) -> bool:
        # Implemented by RegistrationMixin; declared here so other mixins
        # (e.g. the profile mixin's post-change re-verification) type-check.
        raise NotImplementedError

    blacklist: Any = None
    deletion_log: Any = None
    passkeys: Any = None
    # Precomputed dummy Argon2id hash (set by AuthManager.__init__) used to
    # equalize response timing on the user-not-found path.
    _dummy_password_hash: str = ""

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...
    def _log_audit(
        self,
        event_type: Any,
        user_id: Optional[int],
        success: bool,
        ip_address: Optional[str] = None,
        device_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None: ...
    def _track_ip(self, user_id: int, ip: str) -> None: ...
    def _track_device(self, user_id: int, info: Dict) -> int: ...
    def is_trusted_device(self, user_id: int, fingerprint: Optional[str]) -> bool: ...
    def _ensure_trusted_device_columns(self) -> None: ...
    def trust_device(self, user_id: int, fingerprint: Optional[str]) -> bool: ...
    def untrust_device(self, user_id: int, fingerprint: Optional[str]) -> bool: ...
    def _encrypt_ua(self, ua: Optional[str], context: str) -> Optional[str]: ...
    def _ua_index(self, ua: Optional[str]) -> Optional[str]: ...
    def _json_loads(self, data: str) -> Any: ...

    def get_user(self, user_id: int) -> Optional[User]: ...
    def _create_session(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> Session: ...
    def _create_2fa_challenge(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> TwoFactorChallenge: ...
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
    ) -> User: ...
    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int: ...
    def has_capability(self, token_info: TokenInfo, capability: str) -> bool: ...
