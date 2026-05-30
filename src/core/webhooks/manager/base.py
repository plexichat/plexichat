"""
Webhook manager shared trait and base class.
"""

import hashlib
import secrets
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from src.core.webhooks.exceptions import WebhookAvatarError, WebhookNameError

from .constants import TOKEN_BYTES, WEBHOOK_NAME_MAX_LENGTH


class WebhookManagerTrait(BaseManager):
    """Shared trait providing all common methods for webhook manager mixins."""

    _config: Dict[str, Any]
    _messaging: Optional[Any] = None
    _servers: Optional[Any] = None
    _embeds: Optional[Any] = None

    def _get_channel(self, channel_id: SnowflakeID) -> Optional[dict]:
        """Get channel from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE id = ?", (channel_id,)
        )

    def _get_server(self, server_id: SnowflakeID) -> Optional[dict]:
        """Get server from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_servers WHERE id = ?", (server_id,)
        )

    def _check_manage_webhooks_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has manage_webhooks permission."""
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "webhooks.manage", channel_id
        )

    def _get_channel_webhook_count(self, channel_id: SnowflakeID) -> int:
        """Get count of webhooks in a channel."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM webhook_webhooks WHERE channel_id = ?",
            (channel_id,),
        )
        return row["count"] if row else 0

    def _get_server_webhook_count(self, server_id: SnowflakeID) -> int:
        """Get count of webhooks in a server."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM webhook_webhooks WHERE server_id = ?",
            (server_id,),
        )
        return row["count"] if row else 0

    def _validate_name(self, name: str) -> str:
        """Validate and sanitize webhook name."""
        if not name or not name.strip():
            raise WebhookNameError("Webhook name cannot be empty")

        name = name.strip()

        if len(name) > WEBHOOK_NAME_MAX_LENGTH:
            raise WebhookNameError(
                f"Webhook name cannot exceed {WEBHOOK_NAME_MAX_LENGTH} characters",
                WEBHOOK_NAME_MAX_LENGTH,
            )

        name = re.sub(r"<[^>]*>", "", name)
        name = re.sub(r"javascript:", "", name, flags=re.IGNORECASE)

        name = name.strip()
        if not name:
            raise WebhookNameError(
                "Webhook name contains only restricted characters or becomes empty after sanitization"
            )

        return name

    def _validate_avatar_url(self, url: Optional[str]) -> Optional[str]:
        """Validate avatar URL and prevent SSRF."""
        if not url:
            return None

        url = url.strip()
        if not url:
            return None

        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise WebhookAvatarError("Avatar URL must use http or https")
            if not parsed.netloc:
                raise WebhookAvatarError("Invalid avatar URL")
        except Exception as e:
            if isinstance(e, WebhookAvatarError):
                raise
            raise WebhookAvatarError(f"Invalid avatar URL: {str(e)}")

        if "javascript:" in url.lower() or "data:" in url.lower():
            raise WebhookAvatarError("Invalid avatar URL scheme")

        try:
            from src.utils.security import URLValidator

            validator = URLValidator()
            validator.validate_url_for_request(url)
        except ImportError:
            logger.warning("URLValidator not available for SSRF protection")
        except ValueError as e:
            logger.warning(f"Blocked unsafe webhook avatar URL: {url} - {e}")
            raise WebhookAvatarError(f"Unsafe avatar URL: {str(e)}")

        return url

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

    def _load_config(self) -> Dict[str, Any]:
        """Load webhook configuration from global config."""
        return config.get("webhooks", {})


class WebhookManagerBase(WebhookManagerTrait):
    """Webhook manager base with __init__ for full initialization."""

    def __init__(
        self,
        db: Any,
        auth_module: Any = None,
        messaging_module: Any = None,
        servers_module: Any = None,
        embeds_module: Any = None,
    ) -> None:
        """Initialize the webhook manager base.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for token utilities
            messaging_module: Messaging module for sending messages
            servers_module: Servers module for permission checks
            embeds_module: Embeds module for rich embeds
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._embeds = embeds_module
        self._config = self._load_config()
        logger.info("Webhook module initialized")
