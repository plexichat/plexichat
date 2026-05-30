"""
Webhook execution mixin.
"""

import re
from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.webhooks.models import WebhookMessage
from src.core.webhooks.exceptions import (
    EmbedLimitError,
    InvalidContentError,
    InvalidWebhookTokenError,
)

from .base import WebhookManagerTrait
from .constants import MAX_EMBEDS_PER_MESSAGE, USERNAME_OVERRIDE_MAX_LENGTH


class WebhookExecutionMixin(WebhookManagerTrait):
    """Webhook execution (sending messages)."""

    def execute_webhook(
        self,
        webhook_id: SnowflakeID,
        token: str,
        content: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[SnowflakeID] = None,
        wait: bool = False,
    ) -> Optional[WebhookMessage]:
        """Execute a webhook to send a message."""
        full_token = token
        if not token.startswith("webhook."):
            full_token = self._format_webhook_token(webhook_id, token)

        parsed = self._parse_webhook_token(full_token)
        if not parsed or parsed["webhook_id"] != webhook_id:
            raise InvalidWebhookTokenError("Invalid webhook token")

        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise InvalidWebhookTokenError("Webhook not found")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        if not content and not embeds:
            raise InvalidContentError(
                "Message must have content or embeds", ["empty_message"]
            )

        if content:
            max_length = self._config.get("max_message_length", 2000)
            if len(content) > max_length:
                raise InvalidContentError(
                    f"Content exceeds maximum length of {max_length}",
                    ["content_too_long"],
                )

        if embeds:
            max_embeds = self._config.get(
                "max_embeds_per_message", MAX_EMBEDS_PER_MESSAGE
            )
            if len(embeds) > max_embeds:
                raise EmbedLimitError(
                    f"Maximum {max_embeds} embeds allowed", max_embeds, len(embeds)
                )

        if username:
            username = username.strip()
            if len(username) > USERNAME_OVERRIDE_MAX_LENGTH:
                raise InvalidContentError(
                    f"Username override exceeds maximum length of {USERNAME_OVERRIDE_MAX_LENGTH}",
                    ["username_too_long"],
                )
            username = re.sub(r"<[^>]*>", "", username)

        if avatar_url:
            avatar_url = self._validate_avatar_url(avatar_url)

        channel_id_for_conv = row["channel_id"]
        conversation_id = channel_id_for_conv
        channel_row = self._db.fetch_one(
            "SELECT conversation_id FROM srv_channels WHERE id = ?",
            (channel_id_for_conv,),
        )
        if channel_row and channel_row["conversation_id"]:
            conversation_id = channel_row["conversation_id"]

        now = self._get_timestamp()
        message_id = self._generate_id()

        display_name = username or row["name"]
        display_avatar = avatar_url or row["avatar_url"]

        if self._messaging:
            try:
                msg = self._messaging.send_message(
                    user_id=row["creator_id"],
                    conversation_id=conversation_id,
                    content=content or "",
                    embeds=embeds,
                    webhook_id=webhook_id,
                )
                message_id = msg.id
            except Exception as e:
                logger.error(
                    f"Failed to send webhook message via messaging module: {e}"
                )
                try:
                    self._db.execute(
                        """INSERT INTO msg_messages
                           (id, conversation_id, author_id, content, created_at, updated_at,
                            deleted, edited, webhook_id)
                           VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                        (
                            message_id,
                            conversation_id,
                            row["creator_id"],
                            content or "",
                            now,
                            now,
                            webhook_id,
                        ),
                    )
                except Exception:
                    self._db.execute(
                        """INSERT INTO msg_messages
                           (id, conversation_id, author_id, content, created_at, updated_at,
                            deleted, edited)
                           VALUES (?, ?, ?, ?, ?, ?, 0, 0)""",
                        (
                            message_id,
                            conversation_id,
                            row["creator_id"],
                            content or "",
                            now,
                            now,
                        ),
                    )

        self._db.execute(
            """INSERT INTO webhook_messages
               (id, webhook_id, message_id, channel_id, username_override,
                avatar_override, thread_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._generate_id(),
                webhook_id,
                message_id,
                row["channel_id"],
                username,
                avatar_url,
                thread_id,
                now,
            ),
        )

        if embeds and self._embeds:
            for embed_data in embeds:
                try:
                    embed = self._embeds.create_embed(row["creator_id"], **embed_data)
                    self._embeds.attach_embed_to_message(
                        row["creator_id"], message_id, embed.id
                    )
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
                created_at=now,
            )

        return None

    def execute_webhook_by_url(
        self,
        webhook_url: str,
        content: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[SnowflakeID] = None,
        wait: bool = False,
    ) -> Optional[WebhookMessage]:
        """Execute a webhook using its URL."""
        match = re.match(r"^/?webhooks/(\d+)/(.+)$", webhook_url)
        if not match:
            raise InvalidWebhookTokenError("Invalid webhook URL format")

        webhook_id = int(match.group(1))
        token = match.group(2)

        result = self.execute_webhook(
            webhook_id, token, content, username, avatar_url, embeds, thread_id, wait
        )
        return result
