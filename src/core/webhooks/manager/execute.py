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
    ContentTooLongError,
    InvalidContentError,
    InvalidWebhookContentError,
    InvalidWebhookTokenError,
    RateLimitError,
)

from .base import WebhookManagerTrait
from .constants import MAX_EMBEDS_PER_MESSAGE, USERNAME_OVERRIDE_MAX_LENGTH

# F12 fail-safe default: applied when ``rate_limit_per_minute`` is absent
# or unparseable so a config regression can never disable limiting.
DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE = 30


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
        """Execute a webhook to send a message.

        ``wait=False`` (the default, matching the HTTP API) queues the
        message and returns ``None`` immediately; ``wait=True`` blocks
        until the message is created and returns it.
        """
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
            # Do not disclose whether a webhook id exists to an unauthenticated
            # caller presenting a token. Treat both cases identically.
            raise InvalidWebhookTokenError("Invalid webhook token")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        if not content and not embeds:
            raise InvalidWebhookContentError(
                "Message must have content or embeds", ["empty_message"]
            )

        # F12: the per-webhook execution rate limit must fail SAFE. An
        # absent/invalid/unset config previously disabled limiting entirely
        # (0 = unlimited); a sane default is applied instead so a config
        # regression can never expose webhooks to unbounded floods. An
        # explicit 0/negative is an operator opt-out: honored, but logged.
        rate_limit_raw = self._config.get("rate_limit_per_minute")
        rate_limit: int
        explicitly_disabled = False
        if isinstance(rate_limit_raw, int):
            rate_limit = rate_limit_raw
            explicitly_disabled = rate_limit <= 0
        else:
            try:
                rate_limit = int(str(rate_limit_raw))
            except (TypeError, ValueError):
                rate_limit = DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE
        if explicitly_disabled:
            logger.warning(
                f"Webhook {webhook_id} execution rate limiting is DISABLED "
                "(explicit rate_limit_per_minute <= 0); this is not recommended."
            )
        elif rate_limit <= 0:
            rate_limit = DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE
        if rate_limit > 0:
            from src.core.database.cache import check_rate_limit

            allowed, _remaining = check_rate_limit(
                f"webhook:{webhook_id}:execute", int(rate_limit), 60
            )
            if not allowed:
                raise RateLimitError("Webhook execution rate limit exceeded")

        if content:
            max_length = self._config.get("max_message_length", 2000)
            if len(content) > max_length:
                raise ContentTooLongError(
                    f"Content exceeds maximum length of {max_length}",
                    ["content_too_long"],
                )

        if embeds:
            if not isinstance(embeds, list):
                raise InvalidContentError("Embeds must be a list", ["invalid_embeds"])
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

        # Resolve the webhook's delivery channel and the optional thread target
        # in ONE query. Each execution used to issue two separate srv_channels
        # reads (channel, then thread) that select the same columns and run
        # back-to-back on the hot path. Batching them into a single
        # ``WHERE id IN (...)`` halves the channel-resolution round-trips
        # whenever a thread target is supplied. Both rows are still checked
        # independently below, so the cross-tenant guarantees are unchanged --
        # no mutable state is cached here.
        channel_id_for_conv = row["channel_id"]
        conversation_id = channel_id_for_conv
        resolution_ids: List[Any] = [channel_id_for_conv]
        if thread_id and thread_id not in resolution_ids:
            resolution_ids.append(thread_id)
        placeholders = ",".join("?" for _ in resolution_ids)
        resolved_rows = self._db.fetch_all(
            "SELECT id, server_id, conversation_id FROM srv_channels "
            f"WHERE id IN ({placeholders})",
            tuple(resolution_ids),
        )
        resolved_by_id: Dict[int, Dict[str, Any]] = {}
        for resolved in resolved_rows:
            if resolved.get("id") is not None:
                resolved_by_id[int(resolved["id"])] = resolved

        def _resolved(channel_id: Any) -> Optional[Dict[str, Any]]:
            try:
                return resolved_by_id.get(int(channel_id))
            except (TypeError, ValueError):
                return None

        channel_row = _resolved(channel_id_for_conv)
        # SECURITY: ``row["channel_id"]`` is the webhook's delivery target. It
        # must resolve to a channel on the SAME server as the webhook. Without
        # this check, a webhook record whose channel_id points at another
        # guild's channel (a stale/migrated/partially-updated row) silently
        # posts into that foreign guild's conversation -- cross-tenant message
        # injection by anyone holding the webhook token. Mirror the thread
        # target check below and fail closed on a mismatch.
        if channel_row and int(channel_row["server_id"]) != int(row["server_id"]):
            raise InvalidWebhookContentError(
                "Webhook channel does not belong to the webhook's server",
                ["invalid_channel"],
            )
        if channel_row and channel_row["conversation_id"]:
            conversation_id = channel_row["conversation_id"]

        if thread_id:
            # SECURITY: ``thread_id`` comes from the webhook caller. Letting an
            # arbitrary channel row redirect the delivery to its
            # ``conversation_id`` allowed a webhook bound to guild A to post
            # into a channel belonging to guild B (cross-tenant message
            # injection). A resolved target must belong to the SAME server as
            # the webhook. Ids that resolve to no channel never move the
            # conversation and are left untouched (they are only recorded on
            # the webhook_messages tracking row).
            thread_row = _resolved(thread_id)
            if thread_row and int(thread_row["server_id"]) != int(row["server_id"]):
                raise InvalidWebhookContentError(
                    "Thread does not belong to the webhook's server",
                    ["invalid_thread"],
                )
            if thread_row and thread_row["conversation_id"]:
                # SECURITY (channel scope): the server check above alone let a
                # token holder redirect a webhook bound to channel A into a
                # thread of SIBLING channel B on the same server (e.g. a
                # private/admin-only channel), bypassing the webhook's channel
                # scope. A thread's authoritative parent is the DB row in
                # ``thread_threads`` (``channel_id`` = the channel that owns
                # the thread). Whenever this target would move the delivery
                # conversation, require that parent to be the webhook's own
                # channel -- otherwise fail closed. Ids that resolve to no
                # channel never move the conversation and are left untouched.
                try:
                    thread_pk = int(thread_id)
                    webhook_channel_pk = int(row["channel_id"])
                except (TypeError, ValueError):
                    raise InvalidWebhookContentError(
                        "Invalid thread target for this webhook", ["invalid_thread"]
                    )
                if thread_pk != webhook_channel_pk:
                    parent_channel: Optional[int] = None
                    try:
                        parent_row = self._db.fetch_one(
                            "SELECT channel_id FROM thread_threads WHERE id = ?",
                            (thread_pk,),
                        )
                        if parent_row and parent_row.get("channel_id") is not None:
                            parent_channel = int(parent_row["channel_id"])
                    except Exception as exc:
                        # No authoritative parent -> we cannot prove the
                        # thread belongs to this webhook's channel.
                        logger.warning(
                            f"Webhook thread scope lookup failed for {thread_pk}: {exc}"
                        )
                        parent_channel = None
                    if parent_channel != webhook_channel_pk:
                        raise InvalidWebhookContentError(
                            "Thread does not belong to the webhook's channel",
                            ["invalid_thread"],
                        )
                conversation_id = thread_row["conversation_id"]

        now = self._get_timestamp()
        message_id = self._generate_id()

        display_name = username or row["name"]
        display_avatar = avatar_url or row["avatar_url"]

        # SECURITY: strip persistence-routing keys from webhook-supplied
        # embed dicts. A malicious ``message_id`` (or ``id``) key must
        # never influence which message an embed is attached to; embeds
        # are persisted ONLY via the messaging metadata path below,
        # attached to the message this execution creates.
        safe_embeds: Optional[List[Dict[str, Any]]] = None
        if embeds:
            safe_embeds = [
                (
                    {k: v for k, v in e.items() if k not in ("message_id", "id")}
                    if isinstance(e, dict)
                    else e
                )
                for e in embeds
            ]

        if self._messaging:
            # SECURITY: fail CLOSED if the messaging layer rejects the send.
            # The old code swallowed *every* exception here (including
            # ``ConversationAccessDeniedError`` and ``InvalidContentError``)
            # and inserted a raw row straight into ``msg_messages``. That
            # fallback let a webhook post into a conversation its creator was
            # no longer authorized to write, and persist content the normal
            # send path's XSS/SQLi filter had rejected. A rejected send must
            # propagate instead of being laundered through a raw insert.
            msg = self._messaging.send_message(
                user_id=row["creator_id"],
                conversation_id=conversation_id,
                content=content or "",
                embeds=safe_embeds,
                webhook_id=webhook_id,
            )
            message_id = msg.id

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

        # NOTE: embeds are intentionally NOT replayed through
        # ``self._embeds.create_embed`` here. The messaging metadata path
        # above is the single source of truth for webhook embeds; the
        # previous dual-persistence replay raised TypeError on
        # client-style keys (type/invite_code/color:int) that was then
        # swallowed, so the core copy silently never happened — and a
        # webhook-supplied ``message_id`` could flow into
        # ``attach_embed_to_message`` for an arbitrary message.
        logger.debug(f"Webhook {webhook_id} executed, message {message_id} created")

        if wait:
            return WebhookMessage(
                id=message_id,
                webhook_id=webhook_id,
                channel_id=row["channel_id"],
                content=content,
                username=display_name,
                avatar_url=display_avatar,
                embeds=safe_embeds or [],
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
        # The token segment is the full ``webhook.{id}.{secret}`` token
        # (dots included), so the char class must allow ``.`` as well.
        match = re.match(r"^/?webhooks/(\d+)/([A-Za-z0-9_.-]+)$", webhook_url)
        if not match:
            raise InvalidWebhookTokenError("Invalid webhook URL format")

        webhook_id = int(match.group(1))
        token = match.group(2)

        result = self.execute_webhook(
            webhook_id, token, content, username, avatar_url, embeds, thread_id, wait
        )
        return result
