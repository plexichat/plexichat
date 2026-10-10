import hmac
import hashlib
import time

from ..exceptions import WebhookSignatureError


from .protocol import ApplicationManagerProtocol


class WebhookMixin(ApplicationManagerProtocol):
    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
        timestamp: str,
    ) -> bool:
        max_skew = self._config.get("webhook_signature_max_skew_seconds", 300)
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            raise WebhookSignatureError("Invalid webhook timestamp")
        if abs(int(time.time()) - ts) > max_skew:
            raise WebhookSignatureError("Timestamp outside allowed window")

        secret = self._config.get("webhook_signature_secret", "")
        # SECURITY: fail closed. Without a configured signing secret we
        # cannot prove authenticity, so verification must fail.
        if not secret:
            raise WebhookSignatureError("Webhook signing secret is not configured")

        message = timestamp.encode() + body

        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid webhook signature")

        return True
