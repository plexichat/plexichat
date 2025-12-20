"""
Webhook manager - Core business logic for webhook operations.

Handles creating, managing, and executing webhooks with proper
validation, permission checks, and database interactions.
"""

import re
import time
import secrets
import hashlib
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    Webhook,
    WebhookMessage,
    WebhookType,
)
from .exceptions import (
    WebhookNotFoundError,
    WebhookAccessDeniedError,
    InvalidWebhookTokenError,
    WebhookNameError,
    WebhookAvatarError,
    WebhookLimitError,
    ChannelNotFoundError,
    PermissionDeniedError,
    InvalidContentError,
    EmbedLimitError,
)
from .schema import create_tables


WEBHOOK_NAME_MAX_LENGTH = 80
USERNAME_OVERRIDE_MAX_LENGTH = 80
MAX_EMBEDS_PER_MESSAGE = 10
TOKEN_BYTES = 48


class WebhookManager:
    """Core webhook manager handling all operations."""

    def __init__(self, db, auth_module=None, messaging_module=None, servers_module=None, embeds_module=None):
        """
        Initialize the webhook manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for token utilities
            messaging_module: Messaging module for sending messages
            servers_module: Servers module for permission checks
            embeds_module: Embeds module for rich embeds
        """
        self._db = db
        self._auth = auth_module
        self._messaging = messaging_module
        self._servers = servers_module
        self._embeds = embeds_module
        self._config = self._load_config()

        create_tables(db)

        logger.info("Webhook module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load webhook configuration."""
        defaults = {
            "max_webhooks_per_channel": 10,
            "max_webhooks_per_server": 50,
            "max_message_length": 2000,
            "max_embeds_per_message": 10,
        }

        webhooks_config = config.get("webhooks", {})
        return {**defaults, **webhooks_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _generate_token(self) -> str:
        """Generate a secure webhook token."""
        return secrets.token_urlsafe(TOKEN_BYTES)

    def _hash_token(self, token: str) -> str:
        """Hash a token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()

    def _verify_token(self, token: str, token_hash: str) -> bool:
        """Verify a token against its hash."""
        return secrets.compare_digest(self._hash_token(token), token_hash)

    def _format_webhook_token(self, webhook_id: int, secret: str) -> str:
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

    def _validate_name(self, name: str) -> str:
        """Validate and sanitize webhook name."""
        if not name or not name.strip():
            raise WebhookNameError("Webhook name cannot be empty")

        name = name.strip()

        if len(name) > WEBHOOK_NAME_MAX_LENGTH:
            raise WebhookNameError(
                f"Webhook name cannot exceed {WEBHOOK_NAME_MAX_LENGTH} characters",
                WEBHOOK_NAME_MAX_LENGTH
            )

        name = re.sub(r'<[^>]*>', '', name)
        name = re.sub(r'javascript:', '', name, flags=re.IGNORECASE)

        return name

    def _validate_avatar_url(self, url: Optional[str]) -> Optional[str]:
        """Validate avatar URL."""
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

        return url

    def _get_channel(self, channel_id: int) -> Optional[Dict]:
        """Get channel from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE id = ?",
            (channel_id,)
        )

    def _get_server(self, server_id: int) -> Optional[Dict]:
        """Get server from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_servers WHERE id = ?",
            (server_id,)
        )

    def _check_manage_webhooks_permission(self, user_id: int, server_id: int, channel_id: Optional[int] = None) -> bool:
        """Check if user has manage_webhooks permission."""
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "webhooks.manage", channel_id)

    def _get_channel_webhook_count(self, channel_id: int) -> int:
        """Get count of webhooks in a channel."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM webhook_webhooks WHERE channel_id = ?",
            (channel_id,)
        )
        return row["count"] if row else 0

    def _get_server_webhook_count(self, server_id: int) -> int:
        """Get count of webhooks in a server."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM webhook_webhooks WHERE server_id = ?",
            (server_id,)
        )
        return row["count"] if row else 0

    def create_webhook(
        self,
        user_id: int,
        channel_id: int,
        name: str,
        avatar_url: Optional[str] = None
    ) -> Webhook:
        """
        Create a new webhook for a channel.
        
        Args:
            user_id: ID of user creating webhook
            channel_id: ID of channel for webhook
            name: Webhook name (max 80 chars)
            avatar_url: Optional avatar URL
            
        Returns:
            Created Webhook with token
            
        Raises:
            ChannelNotFoundError: Channel not found
            PermissionDeniedError: No manage_webhooks permission
            WebhookNameError: Invalid name
            WebhookAvatarError: Invalid avatar URL
            WebhookLimitError: Max webhooks reached
        """
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        server_id = channel["server_id"]

        if not self._check_manage_webhooks_permission(user_id, server_id, channel_id):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks",
                "webhooks.manage"
            )

        name = self._validate_name(name)
        avatar_url = self._validate_avatar_url(avatar_url)

        max_per_channel = self._config.get("max_webhooks_per_channel", 10)
        channel_count = self._get_channel_webhook_count(channel_id)
        if channel_count >= max_per_channel:
            raise WebhookLimitError(
                f"Channel has reached maximum of {max_per_channel} webhooks",
                max_per_channel,
                channel_count
            )

        max_per_server = self._config.get("max_webhooks_per_server", 50)
        server_count = self._get_server_webhook_count(server_id)
        if server_count >= max_per_server:
            raise WebhookLimitError(
                f"Server has reached maximum of {max_per_server} webhooks",
                max_per_server,
                server_count
            )

        now = self._get_timestamp()
        webhook_id = self._generate_id()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        self._db.execute(
            """INSERT INTO webhook_webhooks 
               (id, channel_id, server_id, creator_id, name, webhook_type, 
                avatar_url, token_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (webhook_id, channel_id, server_id, user_id, name,
             WebhookType.INCOMING.value, avatar_url, token_hash, now, now)
        )

        logger.debug(f"Webhook {webhook_id} created for channel {channel_id} by user {user_id}")

        return Webhook(
            id=webhook_id,
            channel_id=channel_id,
            server_id=server_id,
            creator_id=user_id,
            name=name,
            webhook_type=WebhookType.INCOMING,
            avatar_url=avatar_url,
            token=full_token,
            created_at=now,
            updated_at=now
        )

    def get_webhook(self, webhook_id: int, user_id: Optional[int] = None) -> Optional[Webhook]:
        """
        Get a webhook by ID.
        
        Args:
            webhook_id: ID of webhook
            user_id: Optional user ID for permission check
            
        Returns:
            Webhook without token (token only shown on create/regenerate)
        """
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        if not row:
            return None

        if user_id is not None:
            if not self._check_manage_webhooks_permission(user_id, row["server_id"], row["channel_id"]):
                raise WebhookAccessDeniedError("You do not have access to this webhook")

        return self._row_to_webhook(row, include_token=False)

    def get_webhook_by_token(self, token: str) -> Optional[Webhook]:
        """
        Get a webhook by its token (for execution).
        
        Args:
            token: Full webhook token
            
        Returns:
            Webhook if token is valid
            
        Raises:
            InvalidWebhookTokenError: Token is invalid
        """
        parsed = self._parse_webhook_token(token)
        if not parsed:
            raise InvalidWebhookTokenError("Invalid webhook token format")

        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (parsed["webhook_id"],)
        )

        if not row:
            raise InvalidWebhookTokenError("Webhook not found")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        return self._row_to_webhook(row, include_token=False)

    def get_channel_webhooks(self, user_id: int, channel_id: int) -> List[Webhook]:
        """
        Get all webhooks for a channel.
        
        Args:
            user_id: ID of user requesting
            channel_id: ID of channel
            
        Returns:
            List of Webhooks without tokens
            
        Raises:
            ChannelNotFoundError: Channel not found
            PermissionDeniedError: No permission
        """
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if not self._check_manage_webhooks_permission(user_id, channel["server_id"], channel_id):
            raise PermissionDeniedError(
                "Missing permission to view webhooks",
                "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE channel_id = ? ORDER BY created_at",
            (channel_id,)
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def get_server_webhooks(self, user_id: int, server_id: int) -> List[Webhook]:
        """
        Get all webhooks for a server.
        
        Args:
            user_id: ID of user requesting
            server_id: ID of server
            
        Returns:
            List of Webhooks without tokens
            
        Raises:
            PermissionDeniedError: No permission
        """
        server = self._get_server(server_id)
        if not server:
            raise WebhookNotFoundError("Server not found")

        if not self._check_manage_webhooks_permission(user_id, server_id):
            raise PermissionDeniedError(
                "Missing permission to view webhooks",
                "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE server_id = ? ORDER BY created_at",
            (server_id,)
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def update_webhook(
        self,
        user_id: int,
        webhook_id: int,
        name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        channel_id: Optional[int] = None
    ) -> Webhook:
        """
        Update a webhook.
        
        Args:
            user_id: ID of user updating
            webhook_id: ID of webhook
            name: New name (optional)
            avatar_url: New avatar URL (optional, empty string to clear)
            channel_id: New channel ID (optional, move webhook)
            
        Returns:
            Updated Webhook
            
        Raises:
            WebhookNotFoundError: Webhook not found
            PermissionDeniedError: No permission
            WebhookNameError: Invalid name
            WebhookAvatarError: Invalid avatar URL
            ChannelNotFoundError: New channel not found
        """
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(user_id, row["server_id"], row["channel_id"]):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks",
                "webhooks.manage"
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

            if not self._check_manage_webhooks_permission(user_id, row["server_id"], channel_id):
                raise PermissionDeniedError(
                    "Missing permission to manage webhooks in target channel",
                    "webhooks.manage"
                )

            max_per_channel = self._config.get("max_webhooks_per_channel", 10)
            channel_count = self._get_channel_webhook_count(channel_id)
            if channel_count >= max_per_channel:
                raise WebhookLimitError(
                    f"Target channel has reached maximum of {max_per_channel} webhooks",
                    max_per_channel,
                    channel_count
                )

            updates.append("channel_id = ?")
            params.append(channel_id)

        if not updates:
            return self._row_to_webhook(row, include_token=False)

        now = self._get_timestamp()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(webhook_id)

        self._db.execute(
            f"UPDATE webhook_webhooks SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )

        logger.debug(f"Webhook {webhook_id} updated by user {user_id}")

        result = self.get_webhook(webhook_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_webhook(self, user_id: int, webhook_id: int) -> bool:
        """
        Delete a webhook.
        
        Args:
            user_id: ID of user deleting
            webhook_id: ID of webhook
            
        Returns:
            True if deleted
            
        Raises:
            WebhookNotFoundError: Webhook not found
            PermissionDeniedError: No permission
        """
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(user_id, row["server_id"], row["channel_id"]):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks",
                "webhooks.manage"
            )

        self._db.execute(
            "DELETE FROM webhook_messages WHERE webhook_id = ?",
            (webhook_id,)
        )

        self._db.execute(
            "DELETE FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        logger.debug(f"Webhook {webhook_id} deleted by user {user_id}")

        return True

    def regenerate_token(self, user_id: int, webhook_id: int) -> Webhook:
        """
        Regenerate a webhook's token.
        
        Args:
            user_id: ID of user regenerating
            webhook_id: ID of webhook
            
        Returns:
            Webhook with new token
            
        Raises:
            WebhookNotFoundError: Webhook not found
            PermissionDeniedError: No permission
        """
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(user_id, row["server_id"], row["channel_id"]):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks",
                "webhooks.manage"
            )

        now = self._get_timestamp()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        self._db.execute(
            "UPDATE webhook_webhooks SET token_hash = ?, updated_at = ? WHERE id = ?",
            (token_hash, now, webhook_id)
        )

        logger.debug(f"Webhook {webhook_id} token regenerated by user {user_id}")

        webhook = self._row_to_webhook(row, include_token=False)
        webhook.token = full_token
        webhook.updated_at = now
        return webhook

    def execute_webhook(
        self,
        webhook_id: int,
        token: str,
        content: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[int] = None,
        wait: bool = False
    ) -> Optional[WebhookMessage]:
        """
        Execute a webhook to send a message.
        
        Args:
            webhook_id: ID of webhook
            token: Webhook token (secret part only or full token)
            content: Message content
            username: Override webhook name for this message
            avatar_url: Override webhook avatar for this message
            embeds: List of embed dictionaries
            thread_id: Optional thread to post to
            wait: If True, return the created message
            
        Returns:
            WebhookMessage if wait=True, else None
            
        Raises:
            InvalidWebhookTokenError: Token is invalid
            InvalidContentError: Content is invalid
            EmbedLimitError: Too many embeds
        """
        full_token = token
        if not token.startswith("webhook."):
            full_token = self._format_webhook_token(webhook_id, token)

        parsed = self._parse_webhook_token(full_token)
        if not parsed or parsed["webhook_id"] != webhook_id:
            raise InvalidWebhookTokenError("Invalid webhook token")

        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?",
            (webhook_id,)
        )

        if not row:
            raise InvalidWebhookTokenError("Webhook not found")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        if not content and not embeds:
            raise InvalidContentError("Message must have content or embeds", ["empty_message"])

        if content:
            max_length = self._config.get("max_message_length", 2000)
            if len(content) > max_length:
                raise InvalidContentError(
                    f"Content exceeds maximum length of {max_length}",
                    ["content_too_long"]
                )

        if embeds:
            max_embeds = self._config.get("max_embeds_per_message", MAX_EMBEDS_PER_MESSAGE)
            if len(embeds) > max_embeds:
                raise EmbedLimitError(
                    f"Maximum {max_embeds} embeds allowed",
                    max_embeds,
                    len(embeds)
                )

        if username:
            username = username.strip()
            if len(username) > USERNAME_OVERRIDE_MAX_LENGTH:
                raise InvalidContentError(
                    f"Username override exceeds maximum length of {USERNAME_OVERRIDE_MAX_LENGTH}",
                    ["username_too_long"]
                )
            username = re.sub(r'<[^>]*>', '', username)

        if avatar_url:
            avatar_url = self._validate_avatar_url(avatar_url)

        channel_id = row["channel_id"]
        if thread_id:
            channel_id = thread_id

        now = self._get_timestamp()
        message_id = self._generate_id()

        display_name = username or row["name"]
        display_avatar = avatar_url or row["avatar_url"]

        if self._messaging:
            try:
                self._db.execute(
                    """INSERT INTO msg_messages 
                       (id, conversation_id, author_id, content, created_at, updated_at, 
                        deleted, edited, webhook_id)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                    (message_id, channel_id, row["creator_id"], content or "",
                     now, now, webhook_id)
                )
            except Exception:
                self._db.execute(
                    """INSERT INTO msg_messages 
                       (id, conversation_id, author_id, content, created_at, updated_at, 
                        deleted, edited)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 0)""",
                    (message_id, channel_id, row["creator_id"], content or "", now, now)
                )

        self._db.execute(
            """INSERT INTO webhook_messages 
               (id, webhook_id, message_id, channel_id, username_override, 
                avatar_override, thread_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self._generate_id(), webhook_id, message_id, row["channel_id"],
             username, avatar_url, thread_id, now)
        )

        if embeds and self._embeds:
            for embed_data in embeds:
                try:
                    embed = self._embeds.create_embed(row["creator_id"], **embed_data)
                    self._embeds.attach_embed_to_message(row["creator_id"], message_id, embed.id)
                except Exception as e:
                    logger.warning(f"Failed to create embed for webhook message: {e}")

        logger.debug(f"Webhook {webhook_id} executed, message {message_id} created")

        if wait:
            return WebhookMessage(
                id=message_id,
                webhook_id=webhook_id,
                channel_id=row["channel_id"],
                content=content,
                username=display_name,
                avatar_url=display_avatar,
                embeds=embeds or [],
                thread_id=thread_id,
                created_at=now
            )

        return None

    def execute_webhook_by_url(
        self,
        webhook_url: str,
        content: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[int] = None,
        wait: bool = False
    ) -> Optional[WebhookMessage]:
        """
        Execute a webhook using its URL.
        
        Args:
            webhook_url: Webhook URL (/webhooks/{id}/{token})
            content: Message content
            username: Override webhook name
            avatar_url: Override webhook avatar
            embeds: List of embed dictionaries
            thread_id: Optional thread to post to
            wait: If True, return the created message
            
        Returns:
            WebhookMessage if wait=True, else None
        """
        match = re.match(r'^/?webhooks/(\d+)/(.+)$', webhook_url)
        if not match:
            raise InvalidWebhookTokenError("Invalid webhook URL format")

        webhook_id = int(match.group(1))
        token = match.group(2)

        result = self.execute_webhook(
            webhook_id, token, content, username, avatar_url, embeds, thread_id, wait
        )
        return result

    def _row_to_webhook(self, row, include_token: bool = False) -> Webhook:
        """Convert database row to Webhook."""
        return Webhook(
            id=row["id"],
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            webhook_type=WebhookType(row["webhook_type"]),
            avatar_url=row["avatar_url"],
            token=None,
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
