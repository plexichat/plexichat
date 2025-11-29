"""
Event manager - Core event dispatch coordination.
"""

from typing import Optional, List, Callable, Set
import threading

import utils.logger as logger

from .models import Event
from .router import EventRouter


class EventManager:
    """Manages event dispatch and subscriptions."""

    def __init__(
        self,
        relationships_module=None,
        servers_module=None,
        messaging_module=None,
    ):
        """
        Initialize the event manager.

        Args:
            relationships_module: For routing presence events
            servers_module: For routing server events
            messaging_module: For routing DM events
        """
        self._router = EventRouter(
            relationships_module=relationships_module,
            servers_module=servers_module,
            messaging_module=messaging_module,
        )
        self._subscribers: List[Callable[[Event, List[int]], None]] = []
        self._lock = threading.Lock()

        logger.info("Events module initialized")

    def subscribe(self, callback: Callable[[Event, List[int]], None]) -> None:
        """
        Subscribe to event dispatches.

        Args:
            callback: Function called with (event, user_ids) when events dispatch
        """
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event, List[int]], None]) -> None:
        """
        Unsubscribe from event dispatches.

        Args:
            callback: Previously subscribed callback
        """
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def dispatch(
        self,
        event: Event,
        user_ids: Optional[List[int]] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        exclude_user_ids: Optional[List[int]] = None,
    ) -> int:
        """
        Dispatch an event to connected users.

        Args:
            event: Event to dispatch
            user_ids: Specific user IDs to send to (if None, uses routing)
            server_id: Server ID for server-scoped events
            channel_id: Channel ID for channel-scoped events
            exclude_user_ids: User IDs to exclude from dispatch

        Returns:
            Number of users the event was dispatched to
        """
        recipients = self._router.get_recipients(
            event=event,
            user_ids=user_ids,
            server_id=server_id,
            channel_id=channel_id,
            exclude_user_ids=exclude_user_ids,
        )

        if not recipients:
            return 0

        with self._lock:
            subscribers = list(self._subscribers)

        for callback in subscribers:
            try:
                callback(event, recipients)
            except Exception as e:
                logger.error(f"Event subscriber error: {e}")

        logger.debug(
            f"Dispatched {event.event_type.value} to {len(recipients)} users"
        )

        return len(recipients)
