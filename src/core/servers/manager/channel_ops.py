from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID

from ..models import (
    ChannelType,
)
from ..exceptions import (
    ChannelNotFoundError,
    ChannelTypeError,
    PermissionDeniedError,
    ServerError,
)
from src.core.servers.permission_utils import (
    has_permission as check_permission,
)
from .protocol import ServerProtocol


class ChannelOpsMixin(ServerProtocol):
    """Mixin for channel messaging operations."""

    def ensure_channel_conversation(
        self, channel_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Return a channel's conversation, repairing a dangling link.

        Server-channel conversations are membership-backed and deliberately
        have no required ``msg_participants`` rows. Older cleanup sweeps could
        therefore hard-delete one while its channel stayed live, after which
        every send failed on the message conversation foreign key. Recreate
        the conversation in place instead of returning 500.
        """
        row = self._db.fetch_one(
            "SELECT id, server_id, channel_type, conversation_id "
            "FROM srv_channels WHERE id = ? AND deleted = 0",
            (channel_id,),
        )
        if row is None:
            return None

        conversation_id = row["conversation_id"]
        if row["channel_type"] not in ("text", "announcement"):
            return conversation_id

        if conversation_id and self._messaging:
            exists = getattr(self._messaging, "conversation_exists", None)
            if exists is not None:
                try:
                    if exists(conversation_id):
                        return conversation_id
                except Exception as exc:
                    logger.warning(
                        "Could not verify conversation %s for channel %s: %s",
                        conversation_id,
                        channel_id,
                        exc,
                    )
                    return conversation_id

        if not self._messaging:
            return conversation_id
        create = getattr(self._messaging, "create_server_channel_conversation", None)
        if create is None:
            return conversation_id

        conv = create(row["server_id"], channel_id)
        self._db.execute(
            "UPDATE srv_channels SET conversation_id = ?, updated_at = ? WHERE id = ?",
            (conv.id, self._get_timestamp(), channel_id),
        )
        self._cache_invalidate(self._channel_cache_prefix, channel_id)
        try:
            from src.core.database import cache_delete, valkey_available

            if valkey_available():
                cache_delete(f"channel:{channel_id}")
                cache_delete(f"server_channels:{row['server_id']}")
        except Exception as exc:
            logger.debug(
                "Channel conversation cache invalidation skipped for %s: %s",
                channel_id,
                exc,
            )
        logger.warning(
            "Repaired missing conversation for server channel %s: %s -> %s",
            channel_id,
            conversation_id,
            conv.id,
        )
        return conv.id

    def repair_dangling_channel_conversations(self) -> int:
        """Recreate conversations for live text channels whose link is gone.

        Runs once when the servers module starts so a database already carrying
        a bad link heals on deploy, not only on the next send attempt.
        """
        rows = self._db.fetch_all(
            "SELECT c.id, c.conversation_id FROM srv_channels c "
            "LEFT JOIN msg_conversations v ON v.id = c.conversation_id "
            "WHERE c.deleted = 0 "
            "AND c.channel_type IN ('text', 'announcement') "
            "AND (c.conversation_id IS NULL OR v.id IS NULL)"
        )
        repaired = 0
        for row in rows:
            try:
                conversation_id = self.ensure_channel_conversation(row["id"])
            except Exception as exc:
                logger.warning(
                    "Could not repair conversation for server channel %s: %s",
                    row["id"],
                    exc,
                )
                continue
            if conversation_id and conversation_id != row["conversation_id"]:
                repaired += 1
        return repaired

    def send_channel_message(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reply_to_id: Optional[SnowflakeID] = None,
    ) -> Any:
        """Send a message to a text channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if channel.channel_type not in (
            ChannelType.TEXT,
            ChannelType.ANNOUNCEMENT,
        ):
            raise ChannelTypeError(
                "Can only send messages to text or announcement channels"
            )

        permissions = self.get_permissions(user_id, channel.server_id, channel_id)
        if not check_permission(permissions, "messages.send"):
            raise PermissionDeniedError(
                "Missing messages.send permission", "messages.send"
            )

        retry_after = self.check_channel_slowmode(user_id, channel_id)
        if retry_after:
            from src.core.ratelimit.exceptions import RateLimitError

            raise RateLimitError(
                f"Slowmode is enabled. Try again in {retry_after:.1f}s",
                retry_after,
            )

        if not self._messaging:
            raise ServerError("Messaging module not available")

        conversation_id = self.ensure_channel_conversation(channel_id)
        if not conversation_id:
            raise ServerError("Channel has no associated conversation")

        return self._messaging.send_message(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            reply_to_id=reply_to_id,
            attachments=attachments,
        )

    def check_channel_slowmode(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[float]:
        """Return the seconds a user must wait before posting, or None.

        Shared by the core send path and the REST send route so both enforce
        the same interval with the same moderator exemptions.  Returns None
        when the channel has no slowmode, the user may bypass it, or the
        channel cannot be resolved (fails open, never blocks a send).
        """
        try:
            channel = self.get_channel(channel_id, user_id)
        except Exception as e:
            logger.debug(f"Slowmode channel lookup failed for {channel_id}: {e}")
            return None

        slowmode_seconds = getattr(channel, "slowmode_seconds", 0) or 0
        if slowmode_seconds <= 0:
            return None

        permissions = {}
        server_id = getattr(channel, "server_id", None)
        if server_id is not None:
            try:
                permissions = self.get_permissions(user_id, server_id, channel_id) or {}
            except Exception as e:
                logger.debug(f"Slowmode permission lookup failed for {channel_id}: {e}")

        for bypass in (
            "messages.bypass_slowmode",
            "messages.manage",
            "channels.manage",
            "administrator",
        ):
            if check_permission(permissions, bypass):
                return None

        return self._check_slowmode(user_id, channel_id, slowmode_seconds)

    def _check_slowmode(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, slowmode_seconds: int
    ) -> Optional[float]:
        """Check if user is slowmoded in channel. Returns retry_after if limited."""
        if slowmode_seconds <= 0:
            return None

        key = f"slowmode:{channel_id}:{user_id}"
        try:
            from src.core.database import cache_get, cache_set

            last_msg_time = cache_get(key)
            now = self._get_timestamp() / 1000.0
            if last_msg_time:
                try:
                    elapsed = now - float(last_msg_time)
                    if elapsed < slowmode_seconds:
                        return slowmode_seconds - elapsed
                except (ValueError, TypeError):
                    pass
            cache_set(key, str(now), ttl=slowmode_seconds)
        except Exception as e:
            # Slowmode is a politeness limit, not an authorization boundary:
            # a cache outage must never stop messages from being delivered.
            logger.debug(f"Slowmode check failed (cache error): {e}")
        return None

    def get_channel_messages(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Any]:
        """Get messages from a text channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if channel.channel_type not in (
            ChannelType.TEXT,
            ChannelType.ANNOUNCEMENT,
        ):
            raise ChannelTypeError(
                "Can only get messages from text or announcement channels"
            )

        self.require_permission(user_id, channel.server_id, "messages.read", channel_id)

        if not self._messaging:
            raise ServerError("Messaging module not available")

        if not channel.conversation_id:
            return []

        messages = self._messaging.get_messages(
            user_id=user_id,
            conversation_id=channel.conversation_id,
            limit=limit,
            before_id=before_id,
            after_id=after_id,
        )

        return messages
