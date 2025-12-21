"""
Tests for scheduled events functionality.
"""

import pytest
import time


@pytest.mark.servers
class TestScheduledEventCreation:
    """Tests for creating scheduled events."""

    def test_create_voice_event(self, server_with_channels):
        """Test creating a voice channel event."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-events",
            channel_type=servers.ChannelType.VOICE,
        )

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Game Night",
            start_time=start_time,
            event_type=servers.ScheduledEventType.VOICE,
            description="Weekly game night",
            channel_id=voice_channel.id,
        )

        assert event is not None
        assert event.name == "Game Night"
        assert event.event_type == servers.ScheduledEventType.VOICE
        assert event.channel_id == voice_channel.id
        assert event.status == servers.ScheduledEventStatus.SCHEDULED

    def test_create_external_event(self, server_with_members):
        """Test creating an external location event."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Meetup",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            description="Community meetup",
            location="Central Park, NYC",
        )

        assert event is not None
        assert event.name == "Meetup"
        assert event.event_type == servers.ScheduledEventType.EXTERNAL
        assert event.location == "Central Park, NYC"
        assert event.channel_id is None

    def test_create_event_with_end_time(self, server_with_channels):
        """Test creating an event with end time."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-test",
            channel_type=servers.ChannelType.VOICE,
        )

        start_time = int(time.time() * 1000) + 3600000
        end_time = start_time + 7200000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Two Hour Event",
            start_time=start_time,
            end_time=end_time,
            event_type=servers.ScheduledEventType.VOICE,
            channel_id=voice_channel.id,
        )

        assert event is not None
        assert event.end_time == end_time

    def test_create_event_requires_permission(self, server_with_members):
        """Test that creating events requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_scheduled_event(
                user_id=member_user.id,
                server_id=server.id,
                name="Unauthorized Event",
                start_time=start_time,
                event_type=servers.ScheduledEventType.EXTERNAL,
                location="Somewhere",
            )

    def test_create_event_past_time_fails(self, server_with_members):
        """Test that creating events in the past fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        past_time = int(time.time() * 1000) - 3600000

        with pytest.raises(servers.InvalidEventTimeError):
            servers.create_scheduled_event(
                user_id=owner.id,
                server_id=server.id,
                name="Past Event",
                start_time=past_time,
                event_type=servers.ScheduledEventType.EXTERNAL,
                location="Somewhere",
            )

    def test_create_external_event_requires_location(self, server_with_members):
        """Test that external events require a location."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        with pytest.raises(servers.ScheduledEventError):
            servers.create_scheduled_event(
                user_id=owner.id,
                server_id=server.id,
                name="No Location Event",
                start_time=start_time,
                event_type=servers.ScheduledEventType.EXTERNAL,
            )


@pytest.mark.servers
class TestScheduledEventRetrieval:
    """Tests for retrieving scheduled events."""

    def test_get_event_by_id(self, server_with_members):
        """Test getting an event by ID."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Test Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Test Location",
        )

        retrieved = servers.get_scheduled_event(event.id, owner.id)
        assert retrieved is not None
        assert retrieved.id == event.id
        assert retrieved.name == "Test Event"

    def test_get_events_for_server(self, server_with_members):
        """Test getting all events for a server."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        for i in range(3):
            servers.create_scheduled_event(
                user_id=owner.id,
                server_id=server.id,
                name=f"Event {i}",
                start_time=start_time + (i * 3600000),
                event_type=servers.ScheduledEventType.EXTERNAL,
                location=f"Location {i}",
            )

        events = servers.get_scheduled_events(owner.id, server.id)
        assert len(events) >= 3

    def test_get_events_by_status(self, server_with_members):
        """Test filtering events by status."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Scheduled Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        events = servers.get_scheduled_events(
            owner.id, server.id, status=servers.ScheduledEventStatus.SCHEDULED
        )
        assert all(e.status == servers.ScheduledEventStatus.SCHEDULED for e in events)

    def test_member_can_view_events(self, server_with_members):
        """Test that members can view events."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Viewable Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        retrieved = servers.get_scheduled_event(event.id, member_user.id)
        assert retrieved is not None


@pytest.mark.servers
class TestScheduledEventUpdate:
    """Tests for updating scheduled events."""

    def test_update_event_name(self, server_with_members):
        """Test updating event name."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Original Name",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        updated = servers.update_scheduled_event(
            user_id=owner.id,
            event_id=event.id,
            name="Updated Name",
        )

        assert updated.name == "Updated Name"

    def test_update_event_status(self, server_with_members):
        """Test updating event status."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Status Test",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        updated = servers.update_scheduled_event(
            user_id=owner.id,
            event_id=event.id,
            status=servers.ScheduledEventStatus.CANCELLED,
        )

        assert updated.status == servers.ScheduledEventStatus.CANCELLED


@pytest.mark.servers
class TestScheduledEventDeletion:
    """Tests for deleting scheduled events."""

    def test_delete_event(self, server_with_members):
        """Test deleting an event."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="To Delete",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        result = servers.delete_scheduled_event(owner.id, event.id)
        assert result is True

        deleted = servers.get_scheduled_event(event.id, owner.id)
        assert deleted is None

    def test_delete_event_requires_permission(self, server_with_members):
        """Test that deleting events requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Protected Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_scheduled_event(member_user.id, event.id)
