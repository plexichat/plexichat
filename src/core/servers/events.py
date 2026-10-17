"""Scheduled event operations - create, get, update, delete events and RSVPs."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import (
    ScheduledEvent,
    EventRSVP,
    ScheduledEventType,
    ScheduledEventStatus,
    RSVPStatus,
)

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def create_scheduled_event(
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
    return _get_manager().create_scheduled_event(
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
    event_id: SnowflakeID, user_id: SnowflakeID
) -> Optional[ScheduledEvent]:
    """Get a scheduled event by ID."""
    return _get_manager().get_scheduled_event(event_id, user_id)


def get_scheduled_events(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    status: Optional[ScheduledEventStatus] = None,
    limit: int = 50,
) -> List[ScheduledEvent]:
    """Get scheduled events for a server."""
    return _get_manager().get_scheduled_events(user_id, server_id, status, limit)


def update_scheduled_event(
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
    return _get_manager().update_scheduled_event(
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


def delete_scheduled_event(user_id: SnowflakeID, event_id: SnowflakeID) -> bool:
    """Delete a scheduled event."""
    return _get_manager().delete_scheduled_event(user_id, event_id)


def rsvp_event(
    user_id: SnowflakeID, event_id: SnowflakeID, status: RSVPStatus
) -> EventRSVP:
    """RSVP to an event."""
    return _get_manager().rsvp_event(user_id, event_id, status)


def remove_rsvp(user_id: SnowflakeID, event_id: SnowflakeID) -> bool:
    """Remove RSVP from an event."""
    return _get_manager().remove_rsvp(user_id, event_id)


def get_event_rsvps(
    user_id: SnowflakeID,
    event_id: SnowflakeID,
    status: Optional[RSVPStatus] = None,
    limit: int = 100,
) -> List[EventRSVP]:
    """Get RSVPs for an event."""
    return _get_manager().get_event_rsvps(user_id, event_id, status, limit)


def generate_recurring_instances(
    event_id: SnowflakeID, user_id: SnowflakeID, count: int = 10
) -> List[ScheduledEvent]:
    """Generate instances of a recurring event."""
    return _get_manager().generate_recurring_instances(event_id, user_id, count)
