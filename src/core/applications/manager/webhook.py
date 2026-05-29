import hmac
import hashlib

from ..exceptions import WebhookSignatureError


from .protocol import ApplicationManagerProtocol


class WebhookMixin(ApplicationManagerProtocol):
    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
        timestamp: str,
    ) -> bool:
        secret = self._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + body

        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid webhook signature")

        return True
