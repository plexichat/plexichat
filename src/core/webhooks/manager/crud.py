"""
Webhook CRUD operations.
"""

import base64
from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID
from src.core.webhooks.models import Webhook, WebhookType
from src.core.webhooks.exceptions import (
    ChannelNotFoundError,
    InvalidWebhookTokenError,
    PermissionDeniedError,
    WebhookAccessDeniedError,
    WebhookLimitError,
    WebhookNotFoundError,
)
from src.utils.encryption import encrypt_data, generate_key_pair

from .base import WebhookManagerTrait


class WebhookCRUDMixin(WebhookManagerTrait):
    """Full webhook CRUD operations."""

    def create_webhook(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        name: str,
        avatar_url: Optional[str] = None,
    ) -> Webhook:
        """Create a new webhook for a channel."""
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        server_id = channel["server_id"]

        if not self._check_manage_webhooks_permission(user_id, server_id, channel_id):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        name = self._validate_name(name)
        avatar_url = self._validate_avatar_url(avatar_url)

        max_per_channel = self._config.get("max_webhooks_per_channel", 100)
        channel_count = self._get_channel_webhook_count(channel_id)
        if channel_count >= max_per_channel:
            raise WebhookLimitError(
                f"Channel has reached maximum of {max_per_channel} webhooks",
                max_per_channel,
                channel_count,
            )

        max_per_server = self._config.get("max_webhooks_per_server", 50)
        server_count = self._get_server_webhook_count(server_id)
        if server_count >= max_per_server:
            raise WebhookLimitError(
                f"Server has reached maximum of {max_per_server} webhooks",
                max_per_server,
                server_count,
            )

        now = self._get_timestamp()
        webhook_id = self._generate_id()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        private_key, public_key = generate_key_pair()
        private_key_encrypted = encrypt_data(
            base64.b64encode(private_key).decode("utf-8"),
            context=f"webhook:{webhook_id}",
        )

        self._db.execute(
            """INSERT INTO webhook_webhooks
               (id, channel_id, server_id, creator_id, name, webhook_type,
                avatar_url, token_hash, signing_key_public, signing_key_private_encrypted, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                webhook_id,
                channel_id,
                server_id,
                user_id,
                name,
                WebhookType.INCOMING.value,
                avatar_url,
                token_hash,
                public_key,
                private_key_encrypted,
                now,
                now,
            ),
        )

        return Webhook(
            id=webhook_id,
            channel_id=channel_id,
            server_id=server_id,
            creator_id=user_id,
            name=name,
            webhook_type=WebhookType.INCOMING,
            avatar_url=avatar_url,
            token=full_token,
            signing_key_public=public_key,
            created_at=now,
            updated_at=now,
        )

    def get_webhook(
        self, webhook_id: SnowflakeID, user_id: Optional[SnowflakeID] = None
    ) -> Optional[Webhook]:
        """Get a webhook by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            return None

        if user_id is not None:
            if not self._check_manage_webhooks_permission(
                user_id, row["server_id"], row["channel_id"]
            ):
                raise WebhookAccessDeniedError("You do not have access to this webhook")

        return self._row_to_webhook(row, include_token=False)

    def get_webhook_by_token(self, token: str) -> Optional[Webhook]:
        """Get a webhook by its token (for execution)."""
        parsed = self._parse_webhook_token(token)
        if not parsed:
            raise InvalidWebhookTokenError("Invalid webhook token format")

        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (parsed["webhook_id"],)
        )

        if not row:
            raise InvalidWebhookTokenError("Webhook not found")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        return self._row_to_webhook(row, include_token=False)

    def get_channel_webhooks(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> List[Webhook]:
        """Get all webhooks for a channel."""
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if not self._check_manage_webhooks_permission(
            user_id, channel["server_id"], channel_id
        ):
            raise PermissionDeniedError(
                "Missing permission to view webhooks", "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE channel_id = ? ORDER BY created_at",
            (channel_id,),
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def get_server_webhooks(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> List[Webhook]:
        """Get all webhooks for a server."""
        server = self._get_server(server_id)
        if not server:
            raise WebhookNotFoundError("Server not found")

        if not self._check_manage_webhooks_permission(user_id, server_id):
            raise PermissionDeniedError(
                "Missing permission to view webhooks", "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE server_id = ? ORDER BY created_at",
            (server_id,),
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def update_webhook(
        self,
        user_id: SnowflakeID,
        webhook_id: SnowflakeID,
        name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Webhook:
        """Update a webhook."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        updates = []
        params = []

        if name is not None:
            name = self._validate_name(name)
            updates.append("name = ?")
            params.append(name)

        if avatar_url is not None:
            if avatar_url == "":
                updates.append("avatar_url = NULL")
            else:
                avatar_url = self._validate_avatar_url(avatar_url)
                updates.append("avatar_url = ?")
                params.append(avatar_url)

        if channel_id is not None and channel_id != row["channel_id"]:
            new_channel = self._get_channel(channel_id)
            if not new_channel:
                raise ChannelNotFoundError("Target channel not found")

            if new_channel["server_id"] != row["server_id"]:
                raise PermissionDeniedError("Cannot move webhook to a different server")

            if not self._check_manage_webhooks_permission(
                user_id, row["server_id"], channel_id
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage webhooks in target channel",
                    "webhooks.manage",
                )

            max_per_channel = self._config.get("max_webhooks_per_channel", 10)
            channel_count = self._get_channel_webhook_count(channel_id)
            if channel_count >= max_per_channel:
                raise WebhookLimitError(
                    f"Target channel has reached maximum of {max_per_channel} webhooks",
                    max_per_channel,
                    channel_count,
                )

            updates.append("channel_id = ?")
            params.append(channel_id)

        if not updates:
            return self._row_to_webhook(row, include_token=False)

        now = self._get_timestamp()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(webhook_id)

        allowed_columns = {
            "name",
            "url",
            "secret",
            "events",
            "enabled",
            "avatar_url",
            "channel_id",
        }
        for update in updates:
            col_name = update.split(" = ")[0]
            if col_name == "updated_at":
                continue
            if col_name not in allowed_columns:
                raise ValueError(f"Invalid column name: {col_name}")

        now = self._get_timestamp()
        for update in updates:
            if "name = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET name = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "url = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET url = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "secret = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET secret = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "events = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET events = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "enabled = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET enabled = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "avatar_url = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET avatar_url = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )
            elif "channel_id = ?" in update:
                idx = updates.index(update)
                self._db.execute(
                    "UPDATE webhook_webhooks SET channel_id = ?, updated_at = ? WHERE id = ?",
                    (params[idx], now, webhook_id),
                )

        result = self.get_webhook(webhook_id)
        assert result is not None
        return result

    def delete_webhook(self, user_id: SnowflakeID, webhook_id: SnowflakeID) -> bool:
        """Delete a webhook."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        self._db.execute(
            "DELETE FROM webhook_messages WHERE webhook_id = ?", (webhook_id,)
        )

        self._db.execute("DELETE FROM webhook_webhooks WHERE id = ?", (webhook_id,))

        return True

    def regenerate_token(
        self, user_id: SnowflakeID, webhook_id: SnowflakeID
    ) -> Webhook:
        """Regenerate a webhook's token."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        now = self._get_timestamp()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        self._db.execute(
            "UPDATE webhook_webhooks SET token_hash = ?, updated_at = ? WHERE id = ?",
            (token_hash, now, webhook_id),
        )

        webhook = self._row_to_webhook(row, include_token=False)
        webhook.token = full_token
        webhook.updated_at = now
        return webhook

    def _row_to_webhook(
        self, row: Dict[str, Any], include_token: bool = False
    ) -> Webhook:
        """Convert a database row to a Webhook object."""
        webhook_id = row["id"]

        return Webhook(
            id=webhook_id,
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            webhook_type=WebhookType(row["webhook_type"]),
            avatar_url=row["avatar_url"],
            token=None,
            signing_key_public=row.get("signing_key_public"),
            signing_key_private_encrypted=row.get("signing_key_private_encrypted"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
