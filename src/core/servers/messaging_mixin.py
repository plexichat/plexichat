"""Channel messaging operations mixin."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID


class MessagingMixin:
    """Mixin for channel messaging operations.

    Provides: send_channel_message, get_channel_messages
    """

    _manager: Any = None

    def send_channel_message(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reply_to_id: Optional[SnowflakeID] = None,
    ) -> Any:
        """Send a message to a text channel."""
        return self._manager.send_channel_message(
            user_id, channel_id, content, attachments, reply_to_id
        )

    def get_channel_messages(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Any]:
        """Get messages from a text channel."""
        return self._manager.get_channel_messages(
            user_id, channel_id, limit, before_id, after_id
        )
