from typing import Any, Dict, List, Optional, cast

from src.core.base import SnowflakeID
from ..models import (
    Notification,
    NotificationSettings,
    ChannelNotificationOverride,
    Mention,
    UnreadCount,
)


class NotificationProtocol:
    _db: Any = None
    _auth: Any = None
    _messaging: Any = None
    _servers: Any = None
    _relationships: Any = None
    _presence: Any = None
    _config: Dict[str, Any] = {}

    def _get_timestamp(self) -> int:
        return cast(NotificationProtocol, super())._get_timestamp()

    def _generate_id(self) -> int:
        return cast(NotificationProtocol, super())._generate_id()

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return cast(NotificationProtocol, super())._user_exists(user_id)

    def parse_mentions(self, content: str) -> List[Mention]:
        return cast(NotificationProtocol, super()).parse_mentions(content)

    def validate_mentions(
        self,
        user_id: SnowflakeID,
        mentions: List[Mention],
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> List[Mention]:
        return cast(NotificationProtocol, super()).validate_mentions(
            user_id, mentions, server_id, channel_id
        )

    def get_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> Optional[NotificationSettings]:
        return cast(NotificationProtocol, super()).get_notification_settings(
            user_id, server_id
        )

    def get_notification_settings_bulk(
        self, user_ids: List[SnowflakeID], server_id: Optional[SnowflakeID] = None
    ) -> Dict[SnowflakeID, NotificationSettings]:
        return cast(NotificationProtocol, super()).get_notification_settings_bulk(
            user_ids, server_id
        )

    def get_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[ChannelNotificationOverride]:
        return cast(NotificationProtocol, super()).get_channel_override(
            user_id, channel_id
        )

    def get_channel_overrides_bulk(
        self, user_ids: List[SnowflakeID], channel_id: SnowflakeID
    ) -> Dict[SnowflakeID, ChannelNotificationOverride]:
        return cast(NotificationProtocol, super()).get_channel_overrides_bulk(
            user_ids, channel_id
        )

    def get_notification(self, notification_id: SnowflakeID) -> Optional[Notification]:
        return cast(NotificationProtocol, super()).get_notification(notification_id)

    def get_unread_count(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> UnreadCount:
        return cast(NotificationProtocol, super()).get_unread_count(user_id, server_id)

    def _dispatch_notification_event(
        self,
        user_id: SnowflakeID,
        event_type: Any,
        data: Dict[str, Any],
    ) -> None:
        cast(NotificationProtocol, super())._dispatch_notification_event(
            user_id, event_type, data
        )

    def _update_unread_count(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        is_mention: bool = False,
    ) -> None:
        cast(NotificationProtocol, super())._update_unread_count(
            user_id, conversation_id, server_id, channel_id, is_mention
        )

    def _decrement_mention_count(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID, amount: int = 1
    ) -> None:
        cast(NotificationProtocol, super())._decrement_mention_count(
            user_id, conversation_id, amount
        )

    def _decrement_unread_count(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID, amount: int = 1
    ) -> None:
        cast(NotificationProtocol, super())._decrement_unread_count(
            user_id, conversation_id, amount
        )
