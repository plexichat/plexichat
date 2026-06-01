import time
import json
from typing import Optional, Any, Dict

import utils.config as config
import utils.logger as logger

from src.core.base import BaseManager
from src.utils.encryption import EncryptionManager

from ..blacklist import BlacklistManager
from ..models import (
    AuditEventType,
    AuthStatus,
    AccountType,
)
from ..exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorInvalidError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
    InvalidUsernameError,
    InvalidEmailError,
    PermissionDeniedError,
    BotLimitExceededError,
)
from ..deletion_log import DeletionLog
from ..passkeys import PasskeyManager

from .registration import RegistrationMixin
from .session import SessionMixin
from .twofactor import TwoFactorMixin
from .password import PasswordMixin
from .bot import BotMixin
from .api_token import ApiTokenMixin
from .profile import ProfileMixin
from .device import DeviceMixin
from .ip_blacklist import IpBlacklistMixin
from .audit import AuditMixin
from .oauth import OAuthMixin
from .deletion import DeletionMixin
from .passkey import PasskeyMixin


class AuthManager(
    BaseManager,
    RegistrationMixin,
    SessionMixin,
    TwoFactorMixin,
    PasswordMixin,
    BotMixin,
    ApiTokenMixin,
    ProfileMixin,
    DeviceMixin,
    IpBlacklistMixin,
    AuditMixin,
    OAuthMixin,
    DeletionMixin,
    PasskeyMixin,
):
    Status = AuthStatus
    AuthStatus = AuthStatus
    AuditEventType = AuditEventType
    AccountType = AccountType

    AuthError = AuthError
    InvalidCredentialsError = InvalidCredentialsError
    AccountLockedError = AccountLockedError
    TokenExpiredError = TokenExpiredError
    TokenInvalidError = TokenInvalidError
    TwoFactorInvalidError = TwoFactorInvalidError
    UserExistsError = UserExistsError
    UserNotFoundError = UserNotFoundError
    WeakPasswordError = WeakPasswordError
    InvalidUsernameError = InvalidUsernameError
    InvalidEmailError = InvalidEmailError
    PermissionDeniedError = PermissionDeniedError
    BotLimitExceededError = BotLimitExceededError

    def __init__(self, db, email_sender=None):
        super().__init__(db)
        self.email_sender = email_sender
        encryption_cfg = config.get("encryption", {}).get("argon2", {})
        self.crypto = EncryptionManager(
            argon2_time_cost=encryption_cfg.get("time_cost", 3),
            argon2_memory_cost=encryption_cfg.get("memory_cost", 65536),
            argon2_parallelism=encryption_cfg.get("parallelism", 2),
        )
        self._config = config.get("authentication", {})
        logger.info("Initializing authentication module")
        self.blacklist = BlacklistManager(db)
        self.deletion_log = DeletionLog()
        self.passkeys = PasskeyManager(db, crypto=self.crypto)
        self._ensure_system_user()

    def _json_dumps(self, data: Any) -> str:
        return json.dumps(data)

    def _json_loads(self, data: str) -> Any:
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    def _ensure_system_user(self) -> None:
        now = self._get_timestamp()
        self._db.insert_or_ignore(
            "auth_users",
            [
                "id",
                "account_type",
                "username",
                "email_index",
                "password_hash",
                "permissions",
                "created_at",
                "updated_at",
                "email_verified",
                "account_locked",
                "age_verified",
            ],
            (
                0,
                "system",
                "System",
                "system_index",
                "INVALID_HASH_LOCKED",
                "{}",
                now,
                now,
                1,
                1,
                1,
            ),
        )

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _get_config(self, key: str, default: Any = None) -> Any:
        try:
            return self._config.get(key, default)
        except Exception:
            return config.get(key, default)

    def _encrypt_ua(self, ua: Optional[str], context: str) -> Optional[str]:
        if not ua:
            return None
        return self.crypto.encrypt_data(ua, context=context)

    def _ua_index(self, ua: Optional[str]) -> Optional[str]:
        if not ua:
            return None
        return self.crypto.blind_index(ua, "user_agent")

    def _log_audit(
        self,
        event_type: AuditEventType,
        user_id: Optional[int],
        success: bool,
        ip_address: Optional[str] = None,
        device_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        audit_id = self._generate_id()
        details_encrypted = (
            self.crypto.encrypt_data(json.dumps(details), context=str(audit_id))
            if details
            else None
        )

        ip_index = None
        ip_encrypted = None
        if ip_address:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip_address, context=str(audit_id))

        self._db.insert_or_ignore(
            "auth_audit_log",
            [
                "id",
                "user_id",
                "event_type",
                "ip_index",
                "ip_encrypted",
                "device_id",
                "timestamp",
                "details_encrypted",
                "success",
            ],
            (
                audit_id,
                user_id,
                event_type.value,
                ip_index,
                ip_encrypted,
                device_id,
                self._get_timestamp(),
                details_encrypted,
                1 if success else 0,
            ),
        )

    def _track_ip(self, user_id: int, ip: str):
        record_id = self._generate_id()
        ip_index = self.crypto.fast_blind_index(ip, "ip_address")
        ip_encrypted = self.crypto.encrypt_data(ip, context=str(record_id))

        self._db.insert_or_ignore(
            "auth_known_ips",
            [
                "id",
                "user_id",
                "ip_index",
                "ip_encrypted",
                "first_seen_at",
                "last_seen_at",
            ],
            (
                record_id,
                user_id,
                ip_index,
                ip_encrypted,
                self._get_timestamp(),
                self._get_timestamp(),
            ),
        )

    def _track_device(self, user_id: int, info: Dict) -> int:
        fp = info.get("fingerprint")
        if not fp:
            return 0
        row = self._db.fetch_one(
            "SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
            (user_id, fp),
        )
        now = self._get_timestamp()
        if row:
            self._db.execute(
                "UPDATE auth_devices SET last_seen_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            return row["id"]
        did = self._generate_id()
        name = info.get("name")
        device_type = info.get("type")
        try:
            name_encrypted = (
                self.crypto.encrypt_data(name, context=f"device:{did}")
                if name
                else None
            )
        except Exception as e:
            logger.warning(f"Failed to encrypt device name: {e}")
            name_encrypted = None
        try:
            device_type_encrypted = (
                self.crypto.encrypt_data(device_type, context=f"device:{did}")
                if device_type
                else None
            )
        except Exception as e:
            logger.warning(f"Failed to encrypt device type: {e}")
            device_type_encrypted = None
        try:
            fingerprint_encrypted = self.crypto.encrypt_data(
                fp, context=f"device:{did}"
            )
        except Exception as e:
            logger.warning(f"Failed to encrypt device fingerprint: {e}")
            fingerprint_encrypted = None
        self._db.execute(
            "INSERT INTO auth_devices (id, user_id, fingerprint, name, device_type, name_encrypted, device_type_encrypted, fingerprint_encrypted, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did,
                user_id,
                fp,
                name,
                device_type,
                name_encrypted,
                device_type_encrypted,
                fingerprint_encrypted,
                now,
                now,
            ),
        )
        return did
