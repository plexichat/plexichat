"""Web Push transport (VAPID over the browser push services)."""

import json
from typing import Any, Dict, Optional

import utils.logger as logger

from .base import PushManagerBase


class WebPushMixin(PushManagerBase):
    """Deliver to browser push subscriptions with VAPID."""

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
