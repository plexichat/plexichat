"""
Webhook token generation, hashing, and verification.
"""

import hashlib
import secrets
from typing import Any, Dict, Optional

from src.core.base import SnowflakeID

from .base import WebhookManagerTrait
from .constants import TOKEN_BYTES


class TokenMixin(WebhookManagerTrait):
    """Token generation, hashing, verification, and formatting."""

    def _generate_token(self) -> str:
        """Generate a secure webhook token."""
        return secrets.token_urlsafe(TOKEN_BYTES)

    def _hash_token(self, token: str) -> str:
        """Hash a token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()

    def _verify_token(self, token: str, token_hash: str) -> bool:
        """Verify a token against its hash."""
        return secrets.compare_digest(self._hash_token(token), token_hash)

    def _format_webhook_token(self, webhook_id: SnowflakeID, secret: str) -> str:
        """Format a webhook token: webhook.{id}.{secret}"""
        return f"webhook.{webhook_id}.{secret}"

    def _parse_webhook_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Parse a webhook token into components."""
        if not token:
            return None

        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "webhook":
            return None

        try:
            webhook_id = int(parts[1])
            secret = parts[2]
            return {"webhook_id": webhook_id, "secret": secret}
        except (ValueError, IndexError):
            return None
