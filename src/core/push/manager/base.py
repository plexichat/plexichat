"""Shared state and method contract for the push manager mixins.

``PushManager`` is composed from one mixin per responsibility: the token
lifecycle, the delivery fan-out, and one sender per transport. This module
owns the manager's state (credential probes, the APNs JWT cache) and the
contract the behaviour mixins implement, so no mixin imports another one.
"""

import threading
from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger

# Blind-index scope used for constant-time-ish lookup of encrypted tokens.
PUSH_TOKEN_BLIND_INDEX_SCOPE = "push_token"


class PushManagerBase:
    """Manager state plus the cross-mixin method contract."""

    MAX_TOKENS_PER_USER = 10
    SUPPORTED_PLATFORMS = {"ios", "android", "web"}

    # Token-based JWT cache: ``{ (team_id, key_id): (jwt_string, exp_ts) }``.
    # Populated lazily by ``_send_apns`` so a server restart resets the
    # cache (Apple lets us cache for an hour, after which the token is
    # rejected with ``InvalidProviderToken``).
    _apns_jwt_cache: Dict[Tuple[Any, Any], Tuple[str, int]] = {}
    # Serialises the check-then-refresh in ``_send_apns`` across delivery
    # threads.
    _apns_jwt_lock = threading.Lock()

    def __init__(self, db, notifications_module=None):
        self._db = db
        self._notifications = notifications_module
        self._fcm_available = False
        self._apns_available = False
        self._vapid_available = False
        self._check_push_services()
        self._ensure_push_token_columns()
        self._migrate_legacy_token_rows()

    def _check_push_services(self) -> None:
        """Check if FCM/APNs/VAPID credentials are available."""
        try:
            import utils.config as config

            push_config = config.get("push_notifications", {})
            if push_config.get("fcm_credentials_path") or push_config.get(
                "fcm_api_key"
            ):
                self._fcm_available = True
            if push_config.get("apns_cert_path") or push_config.get("apns_token_path"):
                self._apns_available = True
            if push_config.get("vapid_private_key") and push_config.get(
                "vapid_subject"
            ):
                self._vapid_available = True
        except Exception as e:
            logger.debug(f"Push service availability probe failed: {e}")

    def _ensure_push_token_columns(self) -> None:
        """Add p256dh/auth/token_index columns for Web Push / encryption
        at rest if a pre-existing DB lacks them."""
        # ``PRAGMA table_info`` only exists in SQLite. Probing with it on
        # Postgres raised, the handler returned early, and the columns were
        # never added -- every registration then failed with
        # ``column "p256dh" does not exist``. Use the database's portable
        # column check when it has one, and fall back to a dialect probe.
        for column in ("p256dh", "auth", "token_index"):
            if self._has_push_token_column(column):
                continue
            try:
                self._db.execute(f"ALTER TABLE push_tokens ADD COLUMN {column} TEXT")
            except Exception as e:
                # Another process may have added it concurrently, or the
                # table does not exist yet — either way it is safe to ignore.
                logger.debug(f"Push token column {column!r} not added: {e}")

    def _has_push_token_column(self, column: str) -> bool:
        """Whether ``push_tokens`` already has *column* on this dialect."""
        checker = getattr(self._db, "column_exists", None)
        if callable(checker):
            try:
                return bool(checker("push_tokens", column))
            except Exception:
                return False
        try:
            if getattr(self._db, "type", None) == "postgres":
                rows = self._db.fetch_all(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'push_tokens'"
                )
            else:
                rows = self._db.fetch_all("PRAGMA table_info(push_tokens)")
        except Exception:
            return False
        return column in {row["name"] for row in rows}

    # -- Implemented by the behaviour mixins ------------------------------
    def get_user_tokens(
        self, user_id: int, platform: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def send_push(
        self,
        user_id: int,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        badge: Optional[int] = None,
        sound: str = "default",
        priority: str = "high",
    ) -> int:
        raise NotImplementedError

    def send_bulk_push(
        self,
        user_ids: List[int],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        raise NotImplementedError

    def _send_to_platform(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        badge: Optional[int],
        sound: str,
        priority: str,
    ) -> Optional[bool]:
        raise NotImplementedError

    def _send_web_push(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        raise NotImplementedError

    def _send_fcm(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        raise NotImplementedError

    def _send_apns(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        badge: Optional[int],
        sound: str,
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        raise NotImplementedError

    # -- Helpers implemented by the tokens mixin --------------------------
    @staticmethod
    def _token_blind_index(plain_token: str) -> str:
        raise NotImplementedError

    @staticmethod
    def _plaintext_for_hint(stored_token: Any) -> Optional[str]:
        raise NotImplementedError

    def _migrate_legacy_token_rows(self) -> None:
        raise NotImplementedError

    def _get_timestamp(self) -> int:
        raise NotImplementedError

    def _generate_id(self) -> int:
        raise NotImplementedError
