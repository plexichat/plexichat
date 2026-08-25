"""
Webhook payload signing using Ed25519.
"""

import base64
import hashlib
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

# A captured (signature, timestamp) pair stays valid for up to the full
# replay window after capture; recording it for twice that duration
# guarantees the cache entry outlives any possible reuse of the pair.
_REPLAY_CACHE_TTL_SECONDS = WEBHOOK_REPLAY_WINDOW_SECONDS * 2


def _replay_cache_key(webhook_id: SnowflakeID, signature_header: str) -> str:
    """Cache-tier key for a seen (webhook_id, signature) pair."""
    digest = hashlib.sha256(signature_header.encode("utf-8")).hexdigest()
    return f"webhook:signature-seen:{webhook_id}:{digest}"


class SignatureMixin(WebhookManagerTrait):
    """Webhook payload signing with Ed25519.

    Each webhook is provisioned with an Ed25519 keypair at creation time
    (``signing_key_public`` / ``signing_key_private_encrypted``). ``sign_payload``
    attaches a signature + timestamp to outbound deliveries; ``verify_payload``
    validates inbound deliveries and rejects replays outside
    ``WEBHOOK_REPLAY_WINDOW_SECONDS``.
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

        # Replay protection: a valid (webhook_id, signature) pair seen
        # inside the ±window must never be accepted twice. The cache tier
        # (Valkey with in-memory fallback) records seen pairs with a TTL
        # covering the whole window.
        from src.core.database.cache import cache_get, cache_set

        replay_key = _replay_cache_key(webhook_id, signature_header)
        try:
            if cache_get(replay_key) is not None:
                logger.warning(
                    f"Webhook signature rejected as REPLAY for {webhook_id} "
                    f"(signature/timestamp pair already used within the "
                    f"{WEBHOOK_REPLAY_WINDOW_SECONDS}s window)"
                )
                return False
        except Exception as e:  # cache failure must not bypass the check silently
            logger.error(f"Replay-cache lookup failed for webhook {webhook_id}: {e}")
            return False

        row = self._db.fetch_one(
            "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?",
            (webhook_id,),
        )
        if not row:
            return False

        public_key_bytes = dict(row).get("signing_key_public")
        if not public_key_bytes:
            return False

        # The schema stores the raw 32-byte public key (BLOB/BYTEA). Some
        # rows may carry a base64 text form instead; normalize both so a
        # key written by any version verifies.
        if isinstance(public_key_bytes, str):
            try:
                public_key_bytes = base64.b64decode(public_key_bytes.encode("ascii"))
            except Exception as e:
                logger.error(
                    f"Failed to decode signing-key for inbound webhook "
                    f"{webhook_id}: {e}"
                )
                return False
        elif len(public_key_bytes) != 32:
            logger.error(f"Signing public key for webhook {webhook_id} is not 32 bytes")
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
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature, signed_data
            )
            # Mark this (webhook_id, signature) pair as consumed BEFORE
            # reporting success so a racing duplicate cannot slip through.
            try:
                cache_set(replay_key, True, ttl=_REPLAY_CACHE_TTL_SECONDS)
            except Exception as e:
                logger.error(f"Replay-cache store failed for webhook {webhook_id}: {e}")
                return False
            return True
        except InvalidSignature:
            logger.warning(f"Webhook signature mismatch for {webhook_id}")
            return False
        except Exception as e:
            logger.error(f"Webhook signature verification error for {webhook_id}: {e}")
            return False
