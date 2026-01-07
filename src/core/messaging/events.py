"""
Messaging event bus - Reliable event delivery for messaging operations.

Provides a standardized way to emit and handle messaging events with
optional delivery tracking and retry capabilities.
"""

from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import threading
import time

import utils.logger as logger
from src.core.base import SnowflakeID


class MessagingEventType(Enum):
    """Types of messaging events."""

    MESSAGE_CREATE = "message_create"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_DELETE = "message_delete"
    MESSAGE_ACK = "message_ack"
    MESSAGE_PIN = "message_pin"
    MESSAGE_UNPIN = "message_unpin"
    TYPING_START = "typing_start"
    CONVERSATION_CREATE = "conversation_create"
    CONVERSATION_UPDATE = "conversation_update"
    CONVERSATION_DELETE = "conversation_delete"
    PARTICIPANT_ADD = "participant_add"
    PARTICIPANT_REMOVE = "participant_remove"
    PARTICIPANT_UPDATE = "participant_update"


@dataclass
class MessagingEvent:
    """A messaging event to be dispatched."""

    event_type: MessagingEventType
    data: Dict[str, Any]
    conversation_id: Optional[SnowflakeID] = None
    server_id: Optional[SnowflakeID] = None
    channel_id: Optional[SnowflakeID] = None
    user_ids: Optional[List[SnowflakeID]] = None
    exclude_user_ids: Optional[List[SnowflakeID]] = None
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class EventResult:
    """Result of event dispatch."""

    success: bool
    recipients_count: int
    failed_recipients: List[SnowflakeID] = field(default_factory=list)
    error: Optional[str] = None


# Type alias for event handlers
EventHandler = Callable[[MessagingEvent, List[SnowflakeID]], None]
AsyncEventHandler = Callable[[MessagingEvent, List[SnowflakeID]], Any]


class MessagingEventBus:
    """
    Event bus for messaging operations.

    Supports both synchronous and asynchronous event handling with
    optional delivery tracking.
    """

    def __init__(self, max_retry: int = 3, retry_delay: float = 1.0) -> None:
        """
        Initialize the event bus.

        Args:
            max_retry: Maximum retry attempts for failed deliveries
            retry_delay: Delay between retries in seconds
        """
        self._sync_handlers: List[EventHandler] = []
        self._async_handlers: List[AsyncEventHandler] = []
        self._lock = threading.Lock()
        self._max_retry = max_retry
        self._retry_delay = retry_delay
        self._pending_events: List[MessagingEvent] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Subscribe a synchronous handler to events."""
        with self._lock:
            if handler not in self._sync_handlers:
                self._sync_handlers.append(handler)

    def subscribe_async(self, handler: AsyncEventHandler) -> None:
        """Subscribe an asynchronous handler to events."""
        with self._lock:
            if handler not in self._async_handlers:
                self._async_handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        """Unsubscribe a synchronous handler."""
        with self._lock:
            if handler in self._sync_handlers:
                self._sync_handlers.remove(handler)

    def unsubscribe_async(self, handler: AsyncEventHandler) -> None:
        """Unsubscribe an asynchronous handler."""
        with self._lock:
            if handler in self._async_handlers:
                self._async_handlers.remove(handler)

    def publish(
        self,
        event: MessagingEvent,
        recipients: List[SnowflakeID],
    ) -> EventResult:
        """
        Publish an event synchronously.

        Args:
            event: The event to publish
            recipients: List of user IDs to receive the event

        Returns:
            EventResult with delivery status
        """
        if not recipients:
            return EventResult(success=True, recipients_count=0)

        with self._lock:
            handlers = list(self._sync_handlers)

        failed: List[SnowflakeID] = []
        error_msg: Optional[str] = None

        for handler in handlers:
            try:
                handler(event, recipients)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
                error_msg = str(e)

        return EventResult(
            success=len(failed) == 0 and error_msg is None,
            recipients_count=len(recipients),
            failed_recipients=failed,
            error=error_msg,
        )

    async def publish_async(
        self,
        event: MessagingEvent,
        recipients: List[SnowflakeID],
    ) -> EventResult:
        """
        Publish an event asynchronously.

        Args:
            event: The event to publish
            recipients: List of user IDs to receive the event

        Returns:
            EventResult with delivery status
        """
        if not recipients:
            return EventResult(success=True, recipients_count=0)

        with self._lock:
            async_handlers = list(self._async_handlers)
            sync_handlers = list(self._sync_handlers)

        failed: List[SnowflakeID] = []
        error_msg: Optional[str] = None

        # Run sync handlers
        for handler in sync_handlers:
            try:
                handler(event, recipients)
            except Exception as e:
                logger.error(f"Sync event handler error: {e}")
                error_msg = str(e)

        # Run async handlers
        for handler in async_handlers:
            try:
                result = handler(event, recipients)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Async event handler error: {e}")
                error_msg = str(e)

        return EventResult(
            success=len(failed) == 0 and error_msg is None,
            recipients_count=len(recipients),
            failed_recipients=failed,
            error=error_msg,
        )

    async def publish_with_retry(
        self,
        event: MessagingEvent,
        recipients: List[SnowflakeID],
        max_retries: Optional[int] = None,
    ) -> EventResult:
        """
        Publish an event with automatic retry on failure.

        Args:
            event: The event to publish
            recipients: List of user IDs to receive the event
            max_retries: Override default max retries

        Returns:
            EventResult with delivery status
        """
        retries = max_retries if max_retries is not None else self._max_retry
        last_result: Optional[EventResult] = None

        for attempt in range(retries + 1):
            result = await self.publish_async(event, recipients)

            if result.success:
                return result

            last_result = result

            if attempt < retries:
                logger.warning(
                    f"Event delivery failed (attempt {attempt + 1}/{retries + 1}), retrying..."
                )
                await asyncio.sleep(self._retry_delay)

        return last_result or EventResult(
            success=False,
            recipients_count=len(recipients),
            error="Max retries exceeded",
        )

    def create_event(
        self,
        event_type: MessagingEventType,
        data: Dict[str, Any],
        conversation_id: Optional[SnowflakeID] = None,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        user_ids: Optional[List[SnowflakeID]] = None,
        exclude_user_ids: Optional[List[SnowflakeID]] = None,
    ) -> MessagingEvent:
        """Create a new messaging event."""
        return MessagingEvent(
            event_type=event_type,
            data=data,
            conversation_id=conversation_id,
            server_id=server_id,
            channel_id=channel_id,
            user_ids=user_ids,
            exclude_user_ids=exclude_user_ids,
        )


# Global event bus instance
_event_bus: Optional[MessagingEventBus] = None


def get_event_bus() -> MessagingEventBus:
    """Get or create the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = MessagingEventBus()
    return _event_bus


def set_event_bus(bus: MessagingEventBus) -> None:
    """Set the global event bus instance (for testing)."""
    global _event_bus
    _event_bus = bus
