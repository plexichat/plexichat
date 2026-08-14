"""
Push notification manager - Token management and push delivery.

Handles registering, updating, and invalidating push notification tokens,
as well as preparing and sending push payloads to Firebase Cloud Messaging
and Apple Push Notification service.
"""

import importlib
import json
import time
from typing import Optional, List, Dict, Any, cast

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id
from src.core.push.exceptions import PermissionDeniedError


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
        except Exception:
            pass

    def _ensure_push_token_columns(self) -> None:
        """Add p256dh/auth columns for Web Push if a pre-existing DB lacks them."""
        try:
            existing = {
                row["name"]
                for row in self._db.fetch_all("PRAGMA table_info(push_tokens)")
            }
        except Exception:
            return
        for column in ("p256dh", "auth"):
            if column in existing:
                continue
            try:
                self._db.execute(f"ALTER TABLE push_tokens ADD COLUMN {column} TEXT")
            except Exception:
                # Another process may have added it concurrently, or the
                # table does not exist yet — either way it is safe to ignore.
                pass

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
            Token record dict
        """
        platform = platform.lower()
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {platform}. Use: {self.SUPPORTED_PLATFORMS}"
            )

        now = self._get_timestamp()

        # Check if this token already exists (for any user - re-registration)
        existing = self._db.fetch_one(
            "SELECT id, user_id FROM push_tokens WHERE token = ?",
            (token,),
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
                   app_version = ?, p256dh = ?, auth = ?, updated_at = ?,
                   last_used_at = ?
                   WHERE token = ?""",
                (platform, device_id, app_version, p256dh, auth, now, now, token),
            )
            row = self._db.fetch_one(
                "SELECT * FROM push_tokens WHERE token = ?", (token,)
            )
            return dict(row) if row else {}

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
               (id, user_id, token, platform, device_id, app_version, p256dh, auth, created_at, updated_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                user_id,
                token,
                platform,
                device_id,
                app_version,
                p256dh,
                auth,
                now,
                now,
                now,
            ),
        )

        logger.debug(f"Push token registered for user {user_id} on {platform}")
        row = self._db.fetch_one("SELECT * FROM push_tokens WHERE id = ?", (token_id,))
        return dict(row) if row else {}

    def unregister_token(self, user_id: int, token: str) -> bool:
        """Remove a push notification token."""
        self._db.execute(
            "DELETE FROM push_tokens WHERE user_id = ? AND token = ?",
            (user_id, token),
        )
        return True

    def get_user_tokens(
        self, user_id: int, platform: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all push tokens for a user."""
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
        return [dict(row) for row in rows]

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
            except Exception:
                pass

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
                    "UPDATE push_tokens SET last_used_at = ? WHERE token = ?",
                    (now, token),
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
            self._db.execute("DELETE FROM push_tokens WHERE token = ?", (token,))
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
            return self._send_web_push(token_record, title, body, data, push_config)
        elif platform == "android":
            return self._send_fcm(token, title, body, data, push_config)
        elif platform == "ios":
            return self._send_apns(token, title, body, data, badge, sound, push_config)
        return False

    def _send_web_push(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
    ) -> Optional[bool]:
        """Send a Web Push notification using the VAPID protocol.

        For the ``web`` platform the stored ``token`` is the subscription
        endpoint; the P-256 public key (``p256dh``) and auth secret
        (``auth``) are required to encrypt the payload.  Previously web
        subscriptions were silently handed to FCM as if the endpoint were an
        FCM token, so every browser push was dropped.  Here we encrypt and
        deliver the message directly to the browser push service.

        Returns ``True`` on successful delivery, ``False`` when the
        subscription is rejected by the push service (caller should drop the
        token), and ``None`` when delivery was skipped because VAPID is not
        configured or the subscription is incomplete (token left intact).
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
            return False
        except Exception as e:
            logger.error(f"Web Push failed for {endpoint[:16]}...: {e}")
            return False

    def _send_fcm(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> bool:
        """Send via Firebase Cloud Messaging."""
        if not self._fcm_available:
            # Log that push would have been sent (for debugging)
            logger.debug(f"FCM not configured - would send push to {token[:16]}...")
            return False

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
            return False
        except Exception as e:
            error_str = str(e).lower()
            if (
                "invalid" in error_str
                or "notregistered" in error_str
                or "unregistered" in error_str
            ):
                logger.warning(f"Invalid FCM token: {token[:16]}... - {e}")
                return False
            logger.error(f"FCM push failed: {e}")
            return False

    def _send_apns(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        badge: Optional[int],
        sound: str,
        push_config: Dict[str, Any],
    ) -> bool:
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

        Returns ``True`` on a 200 from APNs, ``False`` on any
        non-fatal error (token unregistered / invalid topic / etc).
        """
        if not self._apns_available:
            logger.debug(f"APNs not configured - would send push to {token[:16]}...")
            return False

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
                return False

            # The user-supplied P8 may have either literal newlines or
            # ``\n`` escape sequences (YAML supports both). Normalise.
            assert isinstance(p8_key, str)
            if "\\n" in p8_key and "\n" not in p8_key:
                p8_key = p8_key.encode().decode("unicode_escape")

            # ---- JWT cache (1-hour lifetime) ----
            cache_key = (team_id, key_id)
            cached = self._apns_jwt_cache.get(cache_key)
            now_ts = int(_time.time())
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
                token_jwt = self._apns_jwt_cache[cache_key][0]
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
                # beyond one level — coerce innocent types here.
                for k, v in data.items():
                    try:
                        payload_dict[str(k)] = v if isinstance(v, str) else str(v)
                    except Exception:
                        continue

            url = (
                "https://api.development.push.apple.com/3/device/"
                if use_sandbox
                else "https://api.push.apple.com/3/device/"
            ) + token
            headers = {
                "authorization": f"bearer {token_jwt}",
                "apns-topic": bundle_id,
                "apns-push-type": "alert",
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
            # ``BadDeviceToken`` / ``Unregistered`` are reasons Apple
            # returns 400 with a ``reason`` JSON field.
            reason = ""
            try:
                reason = response.json().get("reason", "") or ""
            except Exception:
                pass
            logger.warning(
                f"APNs push failed for {token[:16]}… "
                f"(status={response.status_code}, reason={reason})"
            )
            return False

        except ImportError as e:
            # PyJWT or httpx[http2] missing — log actionable detail.
            logger.error(f"APNs push unavailable — install PyJWT + httpx[http2]: {e}")
            return False
        except Exception as e:
            logger.error(f"APNs push failed for {token[:16]}…: {e}")
            return False

    # Token-based JWT cache: ``{ (team_id, key_id): (jwt_string, exp_ts) }``.
    # Populated lazily by ``_send_apns`` so a server restart resets the
    # cache (Apple lets us cache for an hour, after which the token is
    # rejected with ``InvalidProviderToken``).
    _apns_jwt_cache: Dict[tuple, tuple] = {}

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
