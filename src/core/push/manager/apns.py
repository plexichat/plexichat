"""Apple Push Notification service transport (HTTP/2, token-based auth)."""

import json
from typing import Any, Dict, Optional

import utils.logger as logger

from .base import PushManagerBase


class ApnsMixin(PushManagerBase):
    """Deliver to iOS devices through APNs."""

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
            with self._apns_jwt_lock:
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
