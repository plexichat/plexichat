from typing import Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.events.types import EventType


from .protocol import NotificationProtocol


class EventMixin(NotificationProtocol):
    def _dispatch_notification_event(
        self, user_id: SnowflakeID, event_type: EventType, data: Dict[str, Any]
    ):
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event

            if ws_is_setup():
                dispatcher = get_dispatcher()
                event = Event(
                    event_type=event_type,
                    data=data,
                )
                dispatcher.on_event(event, [user_id])
        except Exception as e:
            logger.warning(
                f"Failed to dispatch notification event {event_type} for user {user_id}: {e}"
            )
