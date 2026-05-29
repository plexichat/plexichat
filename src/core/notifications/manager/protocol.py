from typing import Any, Dict, List, Optional

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

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...
    def _user_exists(self, user_id: SnowflakeID) -> bool: ...

    def parse_mentions(self, content: str) -> List[Mention]: ...
    def validate_mentions(
        self,
        user_id: SnowflakeID,
        mentions: List[Mention],
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> List[Mention]: ...

    def get_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> Optional[NotificationSettings]: ...
    def get_notification_settings_bulk(
        self, user_ids: List[SnowflakeID], server_id: Optional[SnowflakeID] = None
    ) -> Dict[SnowflakeID, NotificationSettings]: ...
    def get_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[ChannelNotificationOverride]: ...
    def get_channel_overrides_bulk(
        self, user_ids: List[SnowflakeID], channel_id: SnowflakeID
    ) -> Dict[SnowflakeID, ChannelNotificationOverride]: ...

    def get_notification(
        self, notification_id: SnowflakeID
    ) -> Optional[Notification]: ...

    def get_unread_count(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> UnreadCount: ...

    def _dispatch_notification_event(
        self,
        user_id: SnowflakeID,
        event_type: Any,
        data: Dict[str, Any],
    ) -> None: ...

    def _update_unread_count(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        is_mention: bool = False,
    ) -> None: ...

    def _decrement_mention_count(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> None: ...
