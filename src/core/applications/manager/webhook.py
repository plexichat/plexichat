import hmac
import hashlib
import time

from src.core.security.hardening import bounded_int, reject_control_characters

from ..exceptions import WebhookSignatureError
from .protocol import ApplicationManagerProtocol

MAX_SIGNATURE_LENGTH = 128


class WebhookMixin(ApplicationManagerProtocol):
    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
        timestamp: str,
    ) -> bool:
        max_skew = bounded_int(
            self._config.get("webhook_signature_max_skew_seconds", 300),
            "webhook_signature_max_skew_seconds",
            minimum=0,
            maximum=86_400,
        )
        try:
            signature = reject_control_characters(signature, "signature")
        except ValueError as exc:
            raise WebhookSignatureError("Invalid webhook signature") from exc
        if not signature or len(signature) > MAX_SIGNATURE_LENGTH:
            raise WebhookSignatureError("Invalid webhook signature")
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
