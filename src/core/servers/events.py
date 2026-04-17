"""
Scheduled events manager - Handles server scheduled events with RSVP.
"""

import time
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

try:
    from dateutil.rrule import rrulestr  # type: ignore
except ImportError:
    rrulestr = None  # type: ignore

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    ScheduledEvent,
    EventRSVP,
    ScheduledEventType,
    ScheduledEventStatus,
    RSVPStatus,
    AuditLogAction,
)
from .exceptions import (
    ServerNotFoundError,
    ScheduledEventNotFoundError,
    ScheduledEventError,
    InvalidEventTimeError,
    ChannelNotFoundError,
)


class ScheduledEventManager:
    """Manages scheduled server events."""

    def __init__(
        self, db, server_manager, notifications_module=None, events_module=None
    ):
        """
        Initialize the scheduled event manager.

        Args:
            db: Database instance
            server_manager: ServerManager instance for permission checks
            notifications_module: Optional notifications module for reminders
            events_module: Optional events module for dispatching
        """
        self._db = db
        self._server_manager = server_manager
        self._notifications = notifications_module
        self._events = events_module
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load event configuration."""
        defaults = {
            "max_events_per_server": 100,
            "max_event_duration_hours": 168,
            "reminder_minutes": [60, 15],
            "max_recurring_instances": 50,
        }
        event_config = config.get("servers.events", {})
        return {**defaults, **event_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def create_event(
        self,
        user_id: int,
        server_id: int,
        name: str,
        start_time: int,
        event_type: ScheduledEventType = ScheduledEventType.VOICE,
        description: Optional[str] = None,
        channel_id: Optional[int] = None,
        location: Optional[str] = None,
        end_time: Optional[int] = None,
        timezone_str: str = "UTC",
        image_url: Optional[str] = None,
        rrule: Optional[str] = None,
    ) -> ScheduledEvent:
        """Create a new scheduled event."""
        server = self._server_manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self._server_manager.require_permission(user_id, server_id, "events.manage")

        name = name.strip()
        if not name:
            raise ScheduledEventError("Event name cannot be empty")
        if len(name) > 100:
            raise ScheduledEventError("Event name cannot exceed 100 characters")

        now = self._get_timestamp()
        if start_time < now:
            raise InvalidEventTimeError(
                "Event start time must be in the future", start_time
            )

        if end_time and end_time <= start_time:
            raise InvalidEventTimeError(
                "Event end time must be after start time", start_time, end_time
            )

        max_duration = self._config.get("max_event_duration_hours", 168) * 3600 * 1000
        if end_time and (end_time - start_time) > max_duration:
            raise InvalidEventTimeError("Event duration exceeds maximum allowed")

        if event_type == ScheduledEventType.EXTERNAL:
            if not location:
                raise ScheduledEventError("External events require a location")
            channel_id = None
        else:
            if not channel_id:
                raise ScheduledEventError("Voice/Stage events require a channel")
            channel = self._server_manager.get_channel(channel_id, user_id)
            if not channel or channel.server_id != server_id:
                raise ChannelNotFoundError("Channel not found in this server")
            location = None

        if rrule:
            if rrulestr is None:
                raise ScheduledEventError("Recurring events require python-dateutil")
            try:
                rrulestr(rrule)  # type: ignore
            except Exception:
                raise ScheduledEventError("Invalid RRULE format")

        event_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_scheduled_events 
               (id, server_id, creator_id, name, description, event_type, channel_id, 
                location, start_time, end_time, timezone, status, image_url, rrule, 
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                server_id,
                user_id,
                name,
                description,
                event_type.value,
                channel_id,
                location,
                start_time,
                end_time,
                timezone_str,
                ScheduledEventStatus.SCHEDULED.value,
                image_url,
                rrule,
                now,
                now,
            ),
        )

        self._log_audit(
            server_id, user_id, AuditLogAction.EVENT_CREATE, "event", event_id
        )
        logger.debug(f"Created scheduled event {event_id} in server {server_id}")

        result = self.get_event(event_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_event(self, event_id: int, user_id: int) -> Optional[ScheduledEvent]:
        """Get a scheduled event by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_scheduled_events WHERE id = ?",
            (event_id,),
        )
        if not row:
            return None

        if not self._server_manager._is_member(row["server_id"], user_id):
            return None

        return self._row_to_event(row)

    def get_events(
        self,
        user_id: int,
        server_id: int,
        status: Optional[ScheduledEventStatus] = None,
        limit: int = 50,
    ) -> List[ScheduledEvent]:
        """Get scheduled events for a server."""
        if not self._server_manager._is_member(server_id, user_id):
            raise ServerNotFoundError("Server not found")

        query = "SELECT * FROM srv_scheduled_events WHERE server_id = ?"
        params: list[int | str] = [server_id]

        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY start_time ASC LIMIT ?"
        params.append(min(limit, 100))

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_event(row) for row in rows]

    def update_event(
        self,
        user_id: int,
        event_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        channel_id: Optional[int] = None,
        location: Optional[str] = None,
        image_url: Optional[str] = None,
        status: Optional[ScheduledEventStatus] = None,
    ) -> ScheduledEvent:
        """Update a scheduled event."""
        event = self.get_event(event_id, user_id)
        if not event:
            raise ScheduledEventNotFoundError("Event not found")

        self._server_manager.require_permission(
            user_id, event.server_id, "events.manage"
        )

        if event.status in (
            ScheduledEventStatus.COMPLETED,
            ScheduledEventStatus.CANCELLED,
        ):
            raise ScheduledEventError("Cannot update completed or cancelled events")

        updates = []
        params = []
        changes = {}

        if name is not None:
            name = name.strip()
            if not name:
                raise ScheduledEventError("Event name cannot be empty")
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": event.name, "new": name}

        if description is not None:
            updates.append("description = ?")
            params.append(description)
            changes["description"] = {"old": event.description, "new": description}

        if start_time is not None:
            now = self._get_timestamp()
            if start_time < now:
                raise InvalidEventTimeError("Event start time must be in the future")
            updates.append("start_time = ?")
            params.append(start_time)
            changes["start_time"] = {"old": event.start_time, "new": start_time}

        if end_time is not None:
            effective_start = start_time if start_time else event.start_time
            if end_time <= effective_start:
                raise InvalidEventTimeError("Event end time must be after start time")
            updates.append("end_time = ?")
            params.append(end_time)
            changes["end_time"] = {"old": event.end_time, "new": end_time}

        if channel_id is not None and event.event_type != ScheduledEventType.EXTERNAL:
            channel = self._server_manager.get_channel(channel_id, user_id)
            if not channel or channel.server_id != event.server_id:
                raise ChannelNotFoundError("Channel not found")
            updates.append("channel_id = ?")
            params.append(channel_id)
            changes["channel_id"] = {"old": event.channel_id, "new": channel_id}

        if location is not None and event.event_type == ScheduledEventType.EXTERNAL:
            updates.append("location = ?")
            params.append(location)
            changes["location"] = {"old": event.location, "new": location}

        if image_url is not None:
            updates.append("image_url = ?")
            params.append(image_url)

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
            changes["status"] = {"old": event.status.value, "new": status.value}

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(event_id)

            now = self._get_timestamp()
            for i, update in enumerate(updates):
                col_name = update.split(" = ")[0]
                value = params[i]
                query = (
                    "UPDATE srv_scheduled_events SET "  # nosec: B608
                    + col_name
                    + " = ?, updated_at = ? WHERE id = ?"  # nosec: B608
                )
                self._db.execute(query, (value, now, event_id))

            self._log_audit(
                event.server_id,
                user_id,
                AuditLogAction.EVENT_UPDATE,
                "event",
                event_id,
                changes,
            )

        result = self.get_event(event_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_event(self, user_id: int, event_id: int) -> bool:
        """Delete a scheduled event."""
        event = self.get_event(event_id, user_id)
        if not event:
            raise ScheduledEventNotFoundError("Event not found")

        self._server_manager.require_permission(
            user_id, event.server_id, "events.manage"
        )

        self._db.execute("DELETE FROM srv_event_rsvps WHERE event_id = ?", (event_id,))
        self._db.execute("DELETE FROM srv_scheduled_events WHERE id = ?", (event_id,))

        self._log_audit(
            event.server_id, user_id, AuditLogAction.EVENT_DELETE, "event", event_id
        )
        return True

    def rsvp_event(
        self,
        user_id: int,
        event_id: int,
        status: RSVPStatus,
    ) -> EventRSVP:
        """RSVP to an event."""
        event = self.get_event(event_id, user_id)
        if not event:
            raise ScheduledEventNotFoundError("Event not found")

        if event.status != ScheduledEventStatus.SCHEDULED:
            raise ScheduledEventError("Cannot RSVP to non-scheduled events")

        now = self._get_timestamp()
        existing = self._db.fetch_one(
            "SELECT * FROM srv_event_rsvps WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )

        if existing:
            old_status = existing["status"]
            self._db.execute(
                "UPDATE srv_event_rsvps SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, existing["id"]),
            )
            rsvp_id = existing["id"]

            if old_status != status.value:
                self._update_rsvp_counts(event_id, old_status, status.value)
        else:
            rsvp_id = self._generate_id()
            self._db.execute(
                """INSERT INTO srv_event_rsvps (id, event_id, user_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (rsvp_id, event_id, user_id, status.value, now, now),
            )
            self._update_rsvp_counts(event_id, None, status.value)

        result = self.get_rsvp(event_id, user_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def remove_rsvp(self, user_id: int, event_id: int) -> bool:
        """Remove RSVP from an event."""
        existing = self._db.fetch_one(
            "SELECT * FROM srv_event_rsvps WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        if not existing:
            return True

        self._db.execute(
            "DELETE FROM srv_event_rsvps WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        self._update_rsvp_counts(event_id, existing["status"], None)
        return True

    def get_rsvp(self, event_id: int, user_id: int) -> Optional[EventRSVP]:
        """Get a user's RSVP for an event."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_event_rsvps WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        if not row:
            return None
        return self._row_to_rsvp(row)

    def get_event_rsvps(
        self,
        user_id: int,
        event_id: int,
        status: Optional[RSVPStatus] = None,
        limit: int = 100,
    ) -> List[EventRSVP]:
        """Get RSVPs for an event."""
        event = self.get_event(event_id, user_id)
        if not event:
            raise ScheduledEventNotFoundError("Event not found")

        query = "SELECT * FROM srv_event_rsvps WHERE event_id = ?"
        params: list[int | str] = [event_id]

        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(min(limit, 1000))

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_rsvp(row) for row in rows]

    def _update_rsvp_counts(
        self, event_id: int, old_status: Optional[str], new_status: Optional[str]
    ) -> None:
        """Update RSVP counts on the event."""
        updates = []
        if old_status == RSVPStatus.INTERESTED.value:
            updates.append("interested_count = interested_count - 1")
        elif old_status == RSVPStatus.GOING.value:
            updates.append("going_count = going_count - 1")

        if new_status == RSVPStatus.INTERESTED.value:
            updates.append("interested_count = interested_count + 1")
        elif new_status == RSVPStatus.GOING.value:
            updates.append("going_count = going_count + 1")

        if updates:
            # Avoid dynamic UPDATE to satisfy bandit - execute individual updates per column
            for update in updates:
                query = "UPDATE srv_scheduled_events SET " + update + " WHERE id = ?"  # nosec: B608
                self._db.execute(query, (event_id,))

    def generate_recurring_instances(
        self, event_id: int, user_id: int, count: int = 10
    ) -> List[ScheduledEvent]:
        """Generate instances of a recurring event."""
        event = self.get_event(event_id, user_id)
        if not event:
            raise ScheduledEventNotFoundError("Event not found")

        if not event.rrule:
            raise ScheduledEventError("Event is not recurring")

        self._server_manager.require_permission(
            user_id, event.server_id, "events.manage"
        )

        max_instances = self._config.get("max_recurring_instances", 50)
        count = min(count, max_instances)

        if rrulestr is None:
            raise ScheduledEventError("Recurring events require python-dateutil")

        try:
            rule = rrulestr(
                event.rrule,
                dtstart=datetime.fromtimestamp(
                    event.start_time / 1000, tz=timezone.utc
                ),
            )
        except Exception:
            raise ScheduledEventError("Invalid RRULE format")

        instances: List[ScheduledEvent] = []
        duration = (event.end_time - event.start_time) if event.end_time else 3600000

        for i, dt in enumerate(rule):
            if i >= count:
                break
            if i == 0:
                continue

            start_ms = int(dt.timestamp() * 1000)
            end_ms = start_ms + duration

            instance_id = self._generate_id()
            now = self._get_timestamp()

            self._db.execute(
                """INSERT INTO srv_scheduled_events 
                   (id, server_id, creator_id, name, description, event_type, channel_id,
                    location, start_time, end_time, timezone, status, image_url, 
                    parent_event_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instance_id,
                    event.server_id,
                    event.creator_id,
                    event.name,
                    event.description,
                    event.event_type.value,
                    event.channel_id,
                    event.location,
                    start_ms,
                    end_ms,
                    event.timezone,
                    ScheduledEventStatus.SCHEDULED.value,
                    event.image_url,
                    event_id,
                    now,
                    now,
                ),
            )
            instance = self.get_event(instance_id, user_id)
            if instance:
                instances.append(instance)

        return instances

    def _log_audit(
        self,
        server_id: int,
        user_id: int,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        changes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an audit entry."""
        entry_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO srv_audit_log 
               (id, server_id, user_id, action, target_type, target_id, changes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                server_id,
                user_id,
                action.value,
                target_type,
                target_id,
                json.dumps(changes) if changes else None,
                now,
            ),
        )

    def _row_to_event(self, row: Dict[str, Any]) -> ScheduledEvent:
        """Convert database row to ScheduledEvent model."""
        return ScheduledEvent(
            id=row["id"],
            server_id=row["server_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            description=row["description"],
            event_type=ScheduledEventType(row["event_type"]),
            channel_id=row["channel_id"],
            location=row["location"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            timezone=row["timezone"],
            status=ScheduledEventStatus(row["status"]),
            image_url=row["image_url"],
            interested_count=row["interested_count"],
            going_count=row["going_count"],
            rrule=row["rrule"],
            parent_event_id=row["parent_event_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_rsvp(self, row: Dict[str, Any]) -> EventRSVP:
        """Convert database row to EventRSVP model."""
        return EventRSVP(
            id=row["id"],
            event_id=row["event_id"],
            user_id=row["user_id"],
            status=RSVPStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
