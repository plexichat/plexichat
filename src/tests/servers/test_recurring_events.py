"""
Tests for recurring scheduled events functionality.
"""

import pytest
import time


@pytest.mark.servers
class TestRecurringEventCreation:
    """Tests for creating recurring events."""

    def test_create_recurring_event_with_rrule(self, server_with_members):
        """Test creating an event with RRULE."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Weekly Meeting",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Conference Room",
            rrule="FREQ=WEEKLY;COUNT=10",
        )

        assert event is not None
        assert event.rrule == "FREQ=WEEKLY;COUNT=10"

    def test_create_daily_recurring_event(self, server_with_members):
        """Test creating a daily recurring event."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Daily Standup",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Virtual",
            rrule="FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
        )

        assert event is not None
        assert "FREQ=DAILY" in event.rrule

    def test_create_monthly_recurring_event(self, server_with_members):
        """Test creating a monthly recurring event."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Monthly Review",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Main Office",
            rrule="FREQ=MONTHLY;BYMONTHDAY=1",
        )

        assert event is not None
        assert "FREQ=MONTHLY" in event.rrule

    def test_invalid_rrule_fails(self, server_with_members):
        """Test that invalid RRULE format fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        with pytest.raises(servers.ScheduledEventError):
            servers.create_scheduled_event(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid Recurring",
                start_time=start_time,
                event_type=servers.ScheduledEventType.EXTERNAL,
                location="Somewhere",
                rrule="INVALID_RRULE_FORMAT",
            )


@pytest.mark.servers
class TestRecurringEventInstances:
    """Tests for generating recurring event instances."""

    def test_generate_recurring_instances(self, server_with_members):
        """Test generating instances from a recurring event."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        end_time = start_time + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Weekly Event",
            start_time=start_time,
            end_time=end_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
            rrule="FREQ=WEEKLY;COUNT=5",
        )

        instances = servers.generate_recurring_instances(event.id, owner.id, count=4)

        assert len(instances) == 4
        for instance in instances:
            assert instance.parent_event_id == event.id
            assert instance.name == event.name

    def test_instances_have_correct_times(self, server_with_members):
        """Test that generated instances have correct start times."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        end_time = start_time + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Daily Event",
            start_time=start_time,
            end_time=end_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
            rrule="FREQ=DAILY;COUNT=5",
        )

        instances = servers.generate_recurring_instances(event.id, owner.id, count=3)

        for i, instance in enumerate(instances):
            expected_start = start_time + ((i + 1) * 86400000)
            assert abs(instance.start_time - expected_start) < 1000

    def test_generate_instances_requires_permission(self, server_with_members):
        """Test that generating instances requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Recurring Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
            rrule="FREQ=WEEKLY;COUNT=5",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.generate_recurring_instances(event.id, member_user.id, count=3)

    def test_generate_instances_non_recurring_fails(self, server_with_members):
        """Test that generating instances for non-recurring event fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Single Event",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
        )

        with pytest.raises(servers.ScheduledEventError):
            servers.generate_recurring_instances(event.id, owner.id, count=3)

    def test_instances_preserve_duration(self, server_with_members):
        """Test that instances preserve the original event duration."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000
        duration = 7200000
        end_time = start_time + duration

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Two Hour Event",
            start_time=start_time,
            end_time=end_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            location="Location",
            rrule="FREQ=WEEKLY;COUNT=5",
        )

        instances = servers.generate_recurring_instances(event.id, owner.id, count=2)

        for instance in instances:
            instance_duration = instance.end_time - instance.start_time
            assert instance_duration == duration

    def test_instances_inherit_properties(self, server_with_members):
        """Test that instances inherit parent event properties."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        start_time = int(time.time() * 1000) + 3600000

        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Recurring with Details",
            start_time=start_time,
            event_type=servers.ScheduledEventType.EXTERNAL,
            description="Detailed description",
            location="Specific Location",
            image_url="https://example.com/image.png",
            rrule="FREQ=WEEKLY;COUNT=5",
        )

        instances = servers.generate_recurring_instances(event.id, owner.id, count=1)

        instance = instances[0]
        assert instance.description == event.description
        assert instance.location == event.location
        assert instance.image_url == event.image_url
        assert instance.event_type == event.event_type
