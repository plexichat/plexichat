"""Scheduled event operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import (
    ScheduledEvent,
    EventRSVP,
    ScheduledEventType,
    ScheduledEventStatus,
    RSVPStatus,
)


class EventMixin:
    """Mixin for scheduled event operations.

    Provides: create_scheduled_event, get_scheduled_event, get_scheduled_events,
    update_scheduled_event, delete_scheduled_event, rsvp_event, remove_rsvp,
    get_event_rsvps, generate_recurring_instances
    """

    _event_manager: Any = None

    def create_scheduled_event(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        start_time: int,
        event_type: ScheduledEventType = ScheduledEventType.VOICE,
        description: Optional[str] = None,
        channel_id: Optional[SnowflakeID] = None,
        location: Optional[str] = None,
        end_time: Optional[int] = None,
        timezone_str: str = "UTC",
        image_url: Optional[str] = None,
        rrule: Optional[str] = None,
    ) -> ScheduledEvent:
        """Create a new scheduled event."""
        return self._event_manager.create_event(
            user_id,
            server_id,
            name,
            start_time,
            event_type,
            description,
            channel_id,
            location,
            end_time,
            timezone_str,
            image_url,
            rrule,
        )

    def get_scheduled_event(
        self, event_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[ScheduledEvent]:
        """Get a scheduled event by ID."""
        return self._event_manager.get_event(event_id, user_id)

    def get_scheduled_events(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        status: Optional[ScheduledEventStatus] = None,
        limit: int = 50,
    ) -> List[ScheduledEvent]:
        """Get scheduled events for a server."""
        return self._event_manager.get_events(user_id, server_id, status, limit)

    def update_scheduled_event(
        self,
        user_id: SnowflakeID,
        event_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        channel_id: Optional[SnowflakeID] = None,
        location: Optional[str] = None,
        image_url: Optional[str] = None,
        status: Optional[ScheduledEventStatus] = None,
    ) -> ScheduledEvent:
        """Update a scheduled event."""
        return self._event_manager.update_event(
            user_id,
            event_id,
            name,
            description,
            start_time,
            end_time,
            channel_id,
            location,
            image_url,
            status,
        )

    def delete_scheduled_event(
        self, user_id: SnowflakeID, event_id: SnowflakeID
    ) -> bool:
        """Delete a scheduled event."""
        return self._event_manager.delete_event(user_id, event_id)

    def rsvp_event(
        self, user_id: SnowflakeID, event_id: SnowflakeID, status: RSVPStatus
    ) -> EventRSVP:
        """RSVP to an event."""
        return self._event_manager.rsvp_event(user_id, event_id, status)

    def remove_rsvp(self, user_id: SnowflakeID, event_id: SnowflakeID) -> bool:
        """Remove RSVP from an event."""
        return self._event_manager.remove_rsvp(user_id, event_id)

    def get_event_rsvps(
        self,
        event_id: SnowflakeID,
        user_id: SnowflakeID,
        status: Optional[RSVPStatus] = None,
        limit: int = 100,
    ) -> List[EventRSVP]:
        """Get RSVPs for an event."""
        return self._event_manager.get_event_rsvps(event_id, user_id, status, limit)

    def generate_recurring_instances(
        self, event_id: SnowflakeID, user_id: SnowflakeID, count: int = 10
    ) -> List[ScheduledEvent]:
        """Generate instances of a recurring event."""
        return self._event_manager.generate_recurring_instances(
            event_id, user_id, count
        )
