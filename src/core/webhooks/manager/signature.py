"""
Webhook payload signing using Ed25519.
"""

import base64
import json
import time
from typing import Dict, Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import utils.logger as logger
from src.core.base import SnowflakeID
from src.utils.encryption import decrypt_data

from .constants import (
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
    SIGNATURE_VERSION,
)
from .base import WebhookManagerTrait

# Maximum clock skew tolerated on inbound webhook timestamp validation.
# 5 minutes is a reasonable upper bound that catches obvious replays
# without rejecting legitimately retried deliveries from slow proxies.
WEBHOOK_REPLAY_WINDOW_SECONDS = 5 * 60


class SignatureMixin(WebhookManagerTrait):
    """Webhook payload signing with Ed25519.

    SECURITY: prior to this revision the module exposed only
    ``sign_payload`` (outbound). The matching ``verify_payload`` was
    not implemented, so inbound webhook deliveries accepted any
    payload. Below adds verification plus replay protection keyed on
    the signed timestamp, so an attacker who steals a single signed
    delivery cannot replay it outside the ``WEBHOOK_REPLAY_WINDOW``.
    """

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

    def verify_payload(
        self,
        webhook_id: SnowflakeID,
        payload: Dict[str, Any],
        signature_header: Optional[str],
        timestamp_header: Optional[str],
        now_ms: Optional[int] = None,
    ) -> bool:
        """Verify an inbound webhook payload signature.

        Returns True only when the signature matches AND the
        timestamp is within ``WEBHOOK_REPLAY_WINDOW_SECONDS`` of
        ``now_ms`` (caller-provided UTC ms; defaults to
        ``int(time.time() * 1000)``).

        Args:
            webhook_id: Webhook whose signing public key will be used.
            payload: Decoded payload dict (must match the bytes that
                were signed).
            signature_header: ``X-Plexichat-Signature`` header value
                (base64 URL-safe or standard).
            timestamp_header: ``X-Plexichat-Timestamp`` header value
                (string milliseconds).
            now_ms: Optional caller-supplied current time in ms.

        Returns:
            True if the signature is valid AND not replayed.

        Raises:
            ValueError: when required inputs are missing or malformed.
        """
        if not signature_header or not timestamp_header:
            return False

        try:
            timestamp = int(timestamp_header)
        except (TypeError, ValueError):
            return False

        current = int(now_ms if now_ms is not None else time.time() * 1000)
        skew_ms = abs(current - timestamp)
        if skew_ms > WEBHOOK_REPLAY_WINDOW_SECONDS * 1000:
            logger.warning(
                f"Webhook signature rejected (timestamp outside "
                f"{WEBHOOK_REPLAY_WINDOW_SECONDS}s window): "
                f"webhook={webhook_id}, skew_ms={skew_ms}"
            )
            return False

        row = self._db.fetch_one(
            "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?",
            (webhook_id,),
        )
        if not row:
            return False

        public_key_b64 = dict(row).get("signing_key_public")
        if not public_key_b64:
            return False

        try:
            public_key = base64.b64decode(public_key_b64)
        except Exception as e:
            logger.error(
                f"Failed to decode signing-key for inbound webhook {webhook_id}: {e}"
            )
            return False

        # Decode the signature (URL-safe or standard base64).
        try:
            try:
                signature = base64.urlsafe_b64decode(signature_header.encode("ascii"))
            except Exception:
                signature = base64.b64decode(signature_header.encode("ascii"))
        except Exception as e:
            logger.warning(
                f"Webhook signature header was not valid base64 for {webhook_id}: {e}"
            )
            return False

        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        signed_data = (
            f"{SIGNATURE_VERSION}.{timestamp}.".encode("utf-8") + payload_bytes
        )

        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                signature, signed_data
            )
            return True
        except InvalidSignature:
            logger.warning(f"Webhook signature mismatch for {webhook_id}")
            return False
        except Exception as e:
            logger.error(f"Webhook signature verification error for {webhook_id}: {e}")
            return False
