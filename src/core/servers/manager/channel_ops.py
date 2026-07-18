from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import redis_available

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

        if channel.slowmode_seconds > 0:
            can_bypass = (
                check_permission(permissions, "messages.bypass_slowmode")
                or check_permission(permissions, "messages.manage")
                or check_permission(permissions, "channels.manage")
                or check_permission(permissions, "administrator")
            )

            if not can_bypass:
                retry_after = self._check_slowmode(
                    user_id, channel_id, channel.slowmode_seconds
                )
                if retry_after:
                    from src.core.ratelimit.exceptions import RateLimitError

                    raise RateLimitError(
                        f"Slowmode is enabled. Try again in {retry_after:.1f}s",
                        retry_after,
                    )

        if not self._messaging:
            raise ServerError("Messaging module not available")

        if not channel.conversation_id:
            raise ServerError("Channel has no associated conversation")

        return self._messaging.send_message(
            user_id=user_id,
            conversation_id=channel.conversation_id,
            content=content,
            reply_to_id=reply_to_id,
            attachments=attachments,
        )

    def _check_slowmode(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, slowmode_seconds: int
    ) -> Optional[float]:
        """Check if user is slowmoded in channel. Returns retry_after if limited."""
        if slowmode_seconds <= 0:
            return None

        key = f"slowmode:{channel_id}:{user_id}"
        if redis_available():
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
                logger.debug(f"Slowmode check failed (Redis error): {e}")
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
