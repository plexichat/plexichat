from typing import Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from .mention_validator import MentionValidationMixin
from .creator import NotificationCreatorMixin
from .settings import SettingsMixin
from .unread import UnreadMixin
from .feed import FeedMixin
from .push import PushMixin
from .event import EventMixin


class NotificationManager(
    MentionValidationMixin,
    NotificationCreatorMixin,
    SettingsMixin,
    UnreadMixin,
    FeedMixin,
    PushMixin,
    EventMixin,
    BaseManager,
):
    """Notification manager composed via mixins."""

    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        relationships_module=None,
        presence_module=None,
    ):
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._presence = presence_module
        self._config = self._load_config()

        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_user_created ON notif_notifications (user_id, created_at DESC)"
        )

        logger.info("Notification module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "content_preview_length": 100,
            "max_notifications_per_page": 100,
            "max_feed_items": 100,
        }

        notif_config = config.get("notifications", {})
        return {**defaults, **notif_config}
