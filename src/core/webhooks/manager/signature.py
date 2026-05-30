"""
Webhook payload signing using Ed25519.
"""

import base64
import json
import time
from typing import Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from src.utils.encryption import decrypt_data

from .constants import SIGNATURE_HEADER, SIGNATURE_TIMESTAMP_HEADER, SIGNATURE_VERSION
from .base import WebhookManagerTrait


class SignatureMixin(WebhookManagerTrait):
    """Webhook payload signing with Ed25519."""

    def sign_payload(
        self, webhook_id: SnowflakeID, payload: Dict[str, Any]
    ) -> Dict[str, str]:
        """Sign a webhook payload using Ed25519.

        Args:
            webhook_id: ID of the webhook
            payload: The payload to sign (will be serialized to JSON)

        Returns:
            Dictionary with signature headers:
            - X-Plexichat-Signature: Ed25519 signature (base64)
            - X-Plexichat-Timestamp: Unix timestamp (milliseconds)
        """
        row = self._db.fetch_one(
            "SELECT signing_key_private_encrypted, signing_key_public FROM webhook_webhooks WHERE id = ?",
            (webhook_id,),
        )

        if not row or not dict(row).get("signing_key_private_encrypted"):
            return {}

        row_dict = dict(row)
        try:
            private_key_b64 = decrypt_data(
                row_dict["signing_key_private_encrypted"],
                context=f"webhook:{webhook_id}",
            )
            private_key = base64.b64decode(private_key_b64)
        except Exception as e:
            logger.error(f"Failed to decrypt webhook signing key for {webhook_id}: {e}")
            return {}

        timestamp = int(time.time() * 1000)
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        signed_data = (
            f"{SIGNATURE_VERSION}.{timestamp}.".encode("utf-8") + payload_bytes
        )

        from src.utils.encryption import sign_data

        signature = sign_data(signed_data, private_key)

        return {
            SIGNATURE_HEADER: base64.b64encode(signature).decode("utf-8"),
            SIGNATURE_TIMESTAMP_HEADER: str(timestamp),
        }
