"""
Push notification manager - Token management and push delivery.

Handles registering, updating, and invalidating push notification tokens,
as well as preparing and sending push payloads to Firebase Cloud Messaging
and Apple Push Notification service.
"""

import importlib
import json
import threading
import time
from typing import Optional, List, Dict, Any, cast

import utils.logger as logger
from src.utils.encryption import blind_index, generate_snowflake_id
from src.core.notifications.manager.push import (
    decrypt_push_token,
    encrypt_push_token,
)
from src.core.push.exceptions import PermissionDeniedError

# Blind-index scope used for constant-time-ish lookup of encrypted tokens.
PUSH_TOKEN_BLIND_INDEX_SCOPE = "push_token"


class PushManager:
    """Core push notification manager."""

    MAX_TOKENS_PER_USER = 10
    SUPPORTED_PLATFORMS = {"ios", "android", "web"}

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

    def _migrate_legacy_token_rows(self) -> None:
        """Re-envelope legacy plaintext token rows (H3 hardening).

        Rows written before encryption-at-rest landed store the raw FCM /
        APNs / Web-Push credential verbatim and have no ``token_index``.
        On startup we re-encrypt them (token + p256dh + auth) and backfill
        the blind index so lookups keep working. Failures are logged and
        skipped — a row we cannot read must never break registration.
        """
        try:
            rows = self._db.fetch_all(
                """SELECT id, token, p256dh, auth FROM push_tokens
                   WHERE token_index IS NULL OR token_index = ''"""
            )
        except Exception:
            return
        for row in rows:
            try:
                plain_token = decrypt_push_token(row["token"])
                if not plain_token:
                    continue
                # Legacy rows store p256dh/auth raw; decrypt_push_token
                # passes them through (with a loud warning) unchanged.
                plain_p256dh = (
                    decrypt_push_token(row["p256dh"]) if row["p256dh"] else None
                )
                plain_auth = decrypt_push_token(row["auth"]) if row["auth"] else None
                self._db.execute(
                    """UPDATE push_tokens
                       SET token = ?, token_index = ?, p256dh = ?, auth = ?
                       WHERE id = ?""",
                    (
                        encrypt_push_token(plain_token),
                        self._token_blind_index(plain_token),
                        encrypt_push_token(plain_p256dh) if plain_p256dh else None,
                        encrypt_push_token(plain_auth) if plain_auth else None,
                        row["id"],
                    ),
                )
                logger.debug(f"Re-encrypted legacy push token row {row['id']} at rest")
            except Exception as e:
                logger.warning(
                    f"Failed to migrate legacy push token row {row.get('id')}: {e}"
                )

    @staticmethod
    def _token_blind_index(plain_token: str) -> str:
        return blind_index(plain_token, PUSH_TOKEN_BLIND_INDEX_SCOPE)

    @staticmethod
    def _plaintext_for_hint(stored_token: Any) -> Optional[str]:
        """Return the plaintext a ``token_hint`` may be derived from.

        ``_sanitize_token_record`` runs on two record shapes:
        ``register_token`` hands it the raw ``push_tokens`` row, where the
        credentials are still wrapped in their ``ENC:v1:`` envelope, while
        ``get_user_tokens`` hands it a record whose credentials were already
        decrypted for delivery. Taking the last characters of the stored
        value therefore published the tail of the *ciphertext* on the
        register path: AES-256-GCM draws a fresh nonce per call, so that
        value rotated on every encrypt and identified nothing about the
        device. Unwrap the envelope first (a no-op for already-decrypted
        records); an unwrappable envelope yields no hint rather than a
        misleading one.
        """
        if not stored_token:
            return None
        value = str(stored_token)
        try:
            if value.startswith(("ENC:", "enc:", "PLAIN:")):
                value = decrypt_push_token(value)
        except Exception as e:
            logger.warning(f"Could not derive a push token hint: {e}")
            return None
        return value or None

    @staticmethod
    def _sanitize_token_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Strip raw credentials from a token record for API responses.

        The stored ``token``, ``p256dh`` and ``auth`` values are device
        credentials; they are never echoed back to clients. A short,
        non-reversible ``token_hint`` (last 4 chars) is exposed instead so
        users can tell devices apart.
        """
        sanitized = {
            k: v
            for k, v in dict(record or {}).items()
            if k not in ("token", "p256dh", "auth", "token_index")
        }
        plain_token = PushManager._plaintext_for_hint((record or {}).get("token"))
        if plain_token:
            sanitized["token_hint"] = f"…{plain_token[-4:]}"
        return sanitized

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def register_token(
        self,
        user_id: int,
        token: str,
        platform: str,
        device_id: Optional[str] = None,
        app_version: Optional[str] = None,
        p256dh: Optional[str] = None,
        auth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a push notification token for a user's device.

        Args:
            user_id: ID of the user
            token: Push notification token (FCM/APNs token or Web Push endpoint)
            platform: Platform type (ios, android, web)
            device_id: Optional device identifier
            app_version: Optional app version string
            p256dh: Web Push subscription P-256 ECDH public key (web only)
            auth: Web Push subscription auth secret (web only)

        Returns:
            Sanitized token record dict (raw token/p256dh/auth credentials
            are never included — see :meth:`_sanitize_token_record`).
        """
        platform = platform.lower()
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {platform}. Use: {self.SUPPORTED_PLATFORMS}"
            )

        now = self._get_timestamp()
        # SECURITY: credentials are encrypted at rest (ENC:v1 envelope);
        # lookups go through a keyed blind index over the plaintext.
        stored_token = encrypt_push_token(token)
        token_idx = self._token_blind_index(token)
        stored_p256dh = encrypt_push_token(p256dh) if p256dh else None
        stored_auth = encrypt_push_token(auth) if auth else None

        # Check if this token already exists (for any user - re-registration)
        existing = self._db.fetch_one(
            "SELECT id, user_id FROM push_tokens WHERE token_index = ?",
            (token_idx,),
        )
        if existing:
            # A push token is a credential bound to the device that generated
            # it. Re-registering a token that already belongs to a *different*
            # user would let account B hijack account A's notifications, so we
            # reject cross-user re-registration outright.
            if existing["user_id"] != user_id:
                raise PermissionDeniedError(
                    "This push token is already registered to another account"
                )
            # Same user re-registering (e.g. refreshed subscription) — update.
            self._db.execute(
                """UPDATE push_tokens SET platform = ?, device_id = ?,
                   app_version = ?, token = ?, p256dh = ?, auth = ?,
                   updated_at = ?, last_used_at = ?
                   WHERE id = ?""",
                (
                    platform,
                    device_id,
                    app_version,
                    stored_token,
                    stored_p256dh,
                    stored_auth,
                    now,
                    now,
                    existing["id"],
                ),
            )
            row = self._db.fetch_one(
                "SELECT * FROM push_tokens WHERE id = ?", (existing["id"],)
            )
            return self._sanitize_token_record(dict(row)) if row else {}

        # Check token limit per user (only enforced when a brand new token row
        # is created — re-registering an existing token never adds a row).
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM push_tokens WHERE user_id = ?",
            (user_id,),
        )
        count = count_row["count"] if count_row else 0
        if count >= self.MAX_TOKENS_PER_USER:
            oldest = self._db.fetch_one(
                "SELECT id FROM push_tokens WHERE user_id = ? ORDER BY updated_at ASC LIMIT 1",
                (user_id,),
            )
            if oldest:
                self._db.execute(
                    "DELETE FROM push_tokens WHERE id = ?", (oldest["id"],)
                )

        token_id = self._generate_id()
        self._db.execute(
            """INSERT INTO push_tokens
               (id, user_id, token, token_index, platform, device_id, app_version,
                p256dh, auth, created_at, updated_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                user_id,
                stored_token,
                token_idx,
                platform,
                device_id,
                app_version,
                stored_p256dh,
                stored_auth,
                now,
                now,
                now,
            ),
        )

        logger.debug(f"Push token registered for user {user_id} on {platform}")
        row = self._db.fetch_one("SELECT * FROM push_tokens WHERE id = ?", (token_id,))
        return self._sanitize_token_record(dict(row)) if row else {}

    def unregister_token(self, user_id: int, token: str) -> bool:
        """Remove a push notification token."""
        self._db.execute(
            "DELETE FROM push_tokens WHERE user_id = ? AND token_index = ?",
            (user_id, self._token_blind_index(token)),
        )
        return True

    def unregister_token_by_id(self, user_id: int, token_id: int) -> bool:
        """Remove a push token row by its id (must belong to ``user_id``).

        List responses only expose the row id plus a non-reversible
        ``token_hint`` — clients unregister devices by id because the raw
        token value is never sent back to them.
        """
        self._db.execute(
            "DELETE FROM push_tokens WHERE user_id = ? AND id = ?",
            (user_id, token_id),
        )
        return True

    def get_user_tokens(
        self, user_id: int, platform: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all push tokens for a user, decrypted for internal delivery.

        The returned records contain the plaintext credentials required by
        FCM/APNs/Web-Push senders — they must NEVER be serialised into an
        API response (use :meth:`_sanitize_token_record` first).
        """
        if platform:
            rows = self._db.fetch_all(
                "SELECT * FROM push_tokens WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT * FROM push_tokens WHERE user_id = ?",
                (user_id,),
            )
        results = []
        for row in rows:
            record = dict(row)
            try:
                record["token"] = decrypt_push_token(record.get("token") or "")
                if record.get("p256dh"):
                    record["p256dh"] = decrypt_push_token(record["p256dh"])
                if record.get("auth"):
                    record["auth"] = decrypt_push_token(record["auth"])
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt push token row {record.get('id')}: {e}"
                )
                continue
            results.append(record)
        return results

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
        """
        Send a push notification to all of a user's devices.

        Args:
            user_id: ID of the target user
            title: Notification title
            body: Notification body text
            data: Optional data payload
            badge: Optional badge count
            sound: Sound name
            priority: Priority level

        Returns:
            Number of devices the notification was sent to
        """
        tokens = self.get_user_tokens(user_id)
        if not tokens:
            return 0

        # Check user's notification settings
        if self._notifications:
            try:
                settings = self._notifications.get_notification_settings(user_id)
                if not settings.mobile_push:
                    return 0
            except Exception as e:
                logger.debug(f"Push settings lookup failed, delivering anyway: {e}")

        sent_count = 0
        invalid_tokens = []

        for token_record in tokens:
            token = token_record["token"]
            success = self._send_to_platform(
                token_record, title, body, data, badge, sound, priority
            )

            if success is True:
                sent_count += 1
                # Update last used
                now = self._get_timestamp()
                self._db.execute(
                    "UPDATE push_tokens SET last_used_at = ? WHERE token_index = ?",
                    (now, self._token_blind_index(token)),
                )
            elif success is False:
                # Token is genuinely invalid (e.g. subscription expired) -
                # mark for cleanup.
                invalid_tokens.append(token)
            # ``None`` means delivery was skipped because of missing
            # configuration (VAPID not set) or incomplete subscription data;
            # the token is neither counted nor invalidated.

        # Clean up invalid tokens
        for token in invalid_tokens:
            self._db.execute(
                "DELETE FROM push_tokens WHERE token_index = ?",
                (self._token_blind_index(token),),
            )
            logger.debug(f"Removed invalid push token for user {user_id}")

        return sent_count

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
        """Send push notification to a specific platform.

        Returns ``True`` on successful delivery, ``False`` when the token is
        invalid (caller should drop it), and ``None`` when delivery was
        skipped due to missing configuration/subscription data (the token is
        left intact).
        """
        try:
            import utils.config as config

            push_config = config.get("push_notifications", {})
        except Exception:
            push_config = {}

        token = token_record["token"]
        platform = token_record["platform"]
        if platform == "web":
            return self._send_web_push(
                token_record, title, body, data, push_config, priority
            )
        elif platform == "android":
            return self._send_fcm(token, title, body, data, push_config, priority)
        elif platform == "ios":
            return self._send_apns(
                token, title, body, data, badge, sound, push_config, priority
            )
        return False

    def _send_web_push(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        """Send a Web Push notification using the VAPID protocol.

        For the ``web`` platform the stored ``token`` is the subscription
        endpoint; the P-256 public key (``p256dh``) and auth secret
        (``auth``) are required to encrypt the payload.  Previously web
        subscriptions were silently handed to FCM as if the endpoint were an
        FCM token, so every browser push was dropped.  Here we encrypt and
        deliver the message directly to the browser push service.

        Returns ``True`` on successful delivery, ``False`` when the
        subscription is *permanently* rejected by the push service
        (e.g. 404/410 — the caller should drop the token), and ``None`` when
        delivery was skipped because VAPID is not configured, the subscription
        is incomplete, or a *transient* error occurred (network/DNS/TLS).  A
        transient failure must NOT delete an otherwise-valid subscription.
        """
        if not self._vapid_available:
            logger.debug(
                "VAPID not configured - cannot deliver Web Push to "
                f"{token_record.get('token', '')[:16]}..."
            )
            return None

        p256dh = token_record.get("p256dh")
        auth = token_record.get("auth")
        endpoint = token_record.get("token")
        if not p256dh or not auth or not endpoint:
            logger.debug("Web Push subscription missing p256dh/auth/endpoint")
            return None

        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.warning("pywebpush not installed - Web Push unavailable")
            return None

        subscription_info = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }
        payload = json.dumps({"title": title, "body": body, "data": data or {}})
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=push_config["vapid_private_key"],
                vapid_claims={"sub": push_config["vapid_subject"]},
                content_encoding="aes128gcm",
            )
            logger.debug(f"Web Push sent to {endpoint[:16]}...")
            return True
        except WebPushException as e:
            # 404/410 from the push service means the subscription is gone;
            # the caller treats a False return as "remove this token".
            response = getattr(e, "response", None)
            status = getattr(response, "status_code", None)
            logger.warning(
                f"Web Push failed for {endpoint[:16]}... (status={status}, {e})"
            )
            if status in (404, 410):
                return False
            # Any other Web Push error (401, 429, 500, etc.) is transient and
            # must not invalidate an otherwise-valid subscription.
            return None
        except Exception as e:
            # Transient infrastructure failure (DNS, TLS, connection reset).
            # The subscription is still valid; skipping (None) leaves the
            # token intact instead of deleting it.
            logger.warning(f"Web Push skipped (transient) for {endpoint[:16]}...: {e}")
            return None

    def _send_fcm(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        """Send via Firebase Cloud Messaging.

        Returns ``True`` on success, ``False`` when the token is permanently
        invalid (caller drops it), and ``None`` when FCM is unconfigured or a
        transient error occurred (token left intact).
        """
        if not self._fcm_available:
            # Log that push would have been sent (for debugging)
            logger.debug(f"FCM not configured - would send push to {token[:16]}...")
            return None

        try:
            # Try to use firebase-admin SDK
            fcm_messaging = cast(
                Any, importlib.import_module("firebase_admin.messaging")
            )

            message = fcm_messaging.Message(
                token=token,
                notification=fcm_messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={k: str(v) for k, v in (data or {}).items()},
                android=fcm_messaging.AndroidConfig(
                    priority=priority if priority in ("high", "normal") else "high",
                ),
            )

            response = fcm_messaging.send(message)
            logger.debug(f"FCM push sent: {response}")
            return True
        except ImportError:
            logger.warning("firebase-admin not installed - FCM push unavailable")
            return None
        except Exception as e:
            error_str = str(e).lower()
            if (
                "invalid" in error_str
                or "notregistered" in error_str
                or "unregistered" in error_str
            ):
                logger.warning(f"Invalid FCM token: {token[:16]}... - {e}")
                return False
            # Any other FCM error is transient/auth/config related and must not
            # invalidate an otherwise-valid token.
            logger.warning(f"FCM push skipped (transient) for {token[:16]}... - {e}")
            return None

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
        """Send via Apple Push Notification service over HTTP/2 + JWT.

        Replaces the previous ``# TODO: Implement APNs HTTP/2 push`` stub
        which silently dropped every iOS notification.  Plexichat uses
        Apple's token-based authentication (recommended since iOS 13):

        * Build an ES256 JWT from the operator-supplied ``apns_p8_key``
          (the entire PEM contents, escaping newlines into ``\\n``),
          ``apns_key_id`` and ``apns_team_id`` configured under
          ``push_notifications.apns_*``.
        * POST the APNs JSON payload to
          ``https://api.push.apple.com/3/device/<device_token>``.
        * APNs REQUIRES HTTP/2 (falls back to ``api.development``
          for sandbox testing).
        * Each burst of pushes MUST cache the JWT for the lifetime of
          its 1-hour validity — we cache per-(team_id, key_id) inside
          ``self._apns_jwt_cache``.

        Returns ``True`` on a 200 from APNs, ``False`` ONLY when APNs
        itself permanently rejected the token (410 Unregistered /
        BadDeviceToken / etc. — the caller deletes the token), and
        ``None`` when delivery was skipped because APNs is unconfigured,
        misconfigured, or a transient error occurred (the token is left
        intact — an operator-side config problem must never destroy
        valid iOS device registrations).
        """
        if not self._apns_available:
            logger.debug(f"APNs not configured - would send push to {token[:16]}...")
            return None

        try:
            import httpx  # already a project dep
            import jwt  # PyJWT, optional dep — see note below
            import time as _time

            # ---- config sanity ----
            team_id = push_config.get("apns_team_id")
            key_id = push_config.get("apns_key_id")
            bundle_id = push_config.get("apns_bundle_id")
            p8_key = push_config.get("apns_p8_key")
            use_sandbox = bool(push_config.get("apns_use_sandbox", False))
            ttl_seconds = int(push_config.get("apns_ttl_seconds", 3600))

            missing = [
                name
                for name, value in (
                    ("apns_team_id", team_id),
                    ("apns_key_id", key_id),
                    ("apns_bundle_id", bundle_id),
                    ("apns_p8_key", p8_key),
                )
                if not value
            ]
            if missing:
                logger.error(
                    f"APNs push skipped — missing config: {', '.join(missing)}"
                )
                # Misconfigured, not an invalid token: skip (None) so the
                # caller does NOT delete the otherwise-valid iOS token.
                return None

            # The user-supplied P8 may have either literal newlines or
            # ``\n`` escape sequences (env/YAML sources differ). Normalise
            # with a plain replacement: ``unicode_escape`` would also mangle
            # backslashes and non-ASCII bytes elsewhere in the key material.
            assert isinstance(p8_key, str)
            if "\\n" in p8_key and "\n" not in p8_key:
                p8_key = p8_key.replace("\\n", "\n")

            # ---- JWT cache (1-hour lifetime) ----
            # Guarded by a lock: delivery runs on worker threads and the
            # check-then-refresh below must be atomic, otherwise concurrent
            # sends regenerate (and resign) the JWT redundantly.
            cache_key = (team_id, key_id)
            now_ts = int(_time.time())
            with PushManager._apns_jwt_lock:
                cached = self._apns_jwt_cache.get(cache_key)
                if not cached or cached[1] <= now_ts:
                    payload = {"iss": team_id, "iat": now_ts}
                    token_jwt = jwt.encode(
                        payload,
                        p8_key,
                        algorithm="ES256",
                        headers={
                            "kid": key_id
                        },  # cryptography strict-types p8_key but dict[str|None] is validated at runtime
                    )
                    # Refresh 5 minutes before expiry to keep clock-skew safe.
                    self._apns_jwt_cache[cache_key] = (
                        token_jwt,
                        now_ts + max(60, ttl_seconds - 300),
                    )
                else:
                    token_jwt = cached[0]

            # ---- APNs payload (the ``aps`` dict is mandatory) ----
            aps_payload: Dict[str, Any] = {
                "alert": {"title": title, "body": body},
                "sound": sound or "default",
            }
            if badge is not None:
                aps_payload["badge"] = int(badge)
            payload_dict: Dict[str, Any] = {"aps": aps_payload}
            if data:
                # APNs custom keys MUST be strings, ≤4KB, no nested dict
                # beyond one level — coerce innocent types here. Mappings
                # and sequences are JSON-encoded (parseable by the app);
                # anything that cannot be coerced is dropped with a debug
                # log rather than silently, so missing keys are traceable.
                for k, v in data.items():
                    try:
                        if isinstance(v, str):
                            payload_dict[str(k)] = v
                        elif isinstance(v, (dict, list)):
                            payload_dict[str(k)] = json.dumps(v)
                        else:
                            payload_dict[str(k)] = str(v)
                    except Exception as e:
                        logger.debug(f"APNs payload key {k!r} dropped: {e}")

            url = (
                "https://api.development.push.apple.com/3/device/"
                if use_sandbox
                else "https://api.push.apple.com/3/device/"
            ) + token
            apns_priority = "5" if priority != "high" else "10"
            headers = {
                "authorization": f"bearer {token_jwt}",
                "apns-topic": bundle_id,
                "apns-push-type": "alert",
                "apns-priority": apns_priority,
                "apns-expiration": str(now_ts + ttl_seconds),
            }

            # httpx >= 0.20 supports http2=True once the optional
            # ``h2`` package is installed.  Plexichat's requirements
            # already pin ``h2`` transitively via ``httpx[http2]``.
            with httpx.Client(http2=True, timeout=10.0) as client:
                response = client.post(url, json=payload_dict, headers=headers)

            if response.status_code == 200:
                logger.debug(
                    f"APNs push sent to {token[:16]}… ({response.status_code})"
                )
                return True

            # APNs 410 == Unregistered token — caller should delete.
            # ``BadDeviceToken`` / ``Unregistered`` / ``ExpiredToken`` are
            # reasons Apple returns 400 with a ``reason`` JSON field and mean
            # the device token is permanently gone.
            reason = ""
            try:
                reason = (response.json().get("reason", "") or "").lower()
            except Exception as e:
                logger.debug(f"APNs reason parse failed: {e}")
            permanent_reasons = (
                "baddevicetoken",
                "unregistered",
                "expiredtoken",
                "devicetokennotforthisuser",
            )
            if response.status_code == 410 or reason in permanent_reasons:
                logger.warning(
                    f"APNs token permanently invalid for {token[:16]}… "
                    f"(status={response.status_code}, reason={reason})"
                )
                return False

            # Any other status (429 rate-limit, 500/503 transient, 403 auth
            # config, 400 with an unknown reason) is NOT a token problem, so we
            # return ``None`` and leave the subscription intact rather than
            # deleting a valid iOS push token on a transient APNs hiccup.
            logger.warning(
                f"APNs push skipped (transient) for {token[:16]}… "
                f"(status={response.status_code}, reason={reason})"
            )
            return None

        except ImportError as e:
            # PyJWT or httpx[http2] missing — log actionable detail.
            logger.error(f"APNs push unavailable — install PyJWT + httpx[http2]: {e}")
            return None
        except Exception as e:
            logger.warning(f"APNs push skipped (transient) for {token[:16]}…: {e}")
            return None

    # Token-based JWT cache: ``{ (team_id, key_id): (jwt_string, exp_ts) }``.
    # Populated lazily by ``_send_apns`` so a server restart resets the
    # cache (Apple lets us cache for an hour, after which the token is
    # rejected with ``InvalidProviderToken``).
    _apns_jwt_cache: Dict[tuple, tuple] = {}
    # Serialises the check-then-refresh above across delivery threads.
    _apns_jwt_lock = threading.Lock()

    def send_bulk_push(
        self,
        user_ids: List[int],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Send push notification to multiple users."""
        total_sent = 0
        for user_id in user_ids:
            try:
                sent = self.send_push(user_id, title, body, data)
                total_sent += sent
            except Exception as e:
                logger.debug(f"Failed to send push to user {user_id}: {e}")
        return total_sent
