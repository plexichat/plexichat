"""
Webhook manager shared trait and base class.
"""

from typing import Any, Dict, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID


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

    def _load_config(self) -> Dict[str, Any]:
        """Load webhook configuration from global config."""
        return config.get("webhooks", {})

    # ------------------------------------------------------------------ #
    # Cross-mixin method stubs.
    #
    # These methods are defined in sibling mixins (TokenMixin,
    # ValidationMixin, PermissionMixin) and resolved via MRO at runtime
    # in the final WebhookManager composition.  The stubs give pyright a
    # visible declaration on the trait so any mixin that inherits from
    # this trait can call them without a type error.
    # ------------------------------------------------------------------ #

    # --- TokenMixin stubs ---

    def _generate_token(self) -> str: ...

    def _hash_token(self, token: str) -> str: ...

    def _verify_token(self, token: str, token_hash: str) -> bool: ...

    def _format_webhook_token(self, webhook_id: SnowflakeID, secret: str) -> str: ...

    def _parse_webhook_token(self, token: str) -> Optional[Dict[str, Any]]: ...

    # --- ValidationMixin stubs ---

    def _validate_name(self, name: str) -> str: ...

    def _validate_avatar_url(self, url: Optional[str]) -> Optional[str]: ...

    # --- PermissionMixin stubs ---

    def _check_manage_webhooks_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool: ...


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
