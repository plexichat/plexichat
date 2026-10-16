"""Regression tests: template / scheduled-event ``limit`` clamped from below.

``get_user_templates``, ``get_public_templates``, ``get_scheduled_events``
and ``get_event_rsvps`` capped ``limit`` from above (``min(limit, N)``) but
never from below. A negative ``limit`` reached the SQL ``LIMIT ?`` verbatim,
and SQLite treats ``LIMIT -1`` as *unlimited* — so ``limit=-1`` dumped the
whole template / event / RSVP list instead of an empty page.
"""

import time

import pytest


@pytest.mark.servers
class TestTemplatesEventsNegativeLimitClamp:
    def test_get_user_templates_negative_limit_returns_empty(self, server_with_members):
        """A negative template-list limit must not bypass the page cap."""
        server, owner, _admin, _member, _outsider, _role, servers = server_with_members
        servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Clamp Template",
        )
        assert len(servers.get_user_templates(owner.id, limit=10)) >= 1
        assert servers.get_user_templates(owner.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert servers.get_user_templates(owner.id, limit=0) == []

    def test_get_public_templates_negative_limit_returns_empty(
        self, server_with_members
    ):
        """A negative public-template limit must not dump the whole table."""
        server, owner, _admin, _member, _outsider, _role, servers = server_with_members
        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Public Clamp Template",
        )
        servers.update_template(
            user_id=owner.id,
            code=template.code,
            is_public=True,
        )
        assert len(servers.get_public_templates(limit=10)) >= 1
        assert servers.get_public_templates(limit=-1) == []
        assert servers.get_public_templates(limit=0) == []

    def test_get_scheduled_events_negative_limit_returns_empty(
        self, server_with_members
    ):
        """A negative event-list limit must not bypass the page cap."""
        server, owner, _admin, _member, _outsider, _role, servers = server_with_members
        servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Clamp Meetup",
            start_time=int(time.time() * 1000) + 3600000,
            event_type=servers.ScheduledEventType.EXTERNAL,
            description="Clamp test event",
            location="Somewhere",
        )
        assert len(servers.get_scheduled_events(owner.id, server.id, limit=10)) >= 1
        assert servers.get_scheduled_events(owner.id, server.id, limit=-1) == []
        assert servers.get_scheduled_events(owner.id, server.id, limit=0) == []

    def test_get_event_rsvps_negative_limit_returns_empty(self, server_with_members):
        """A negative RSVP-list limit must not dump the whole RSVP list."""
        server, owner, _admin, member_user, _outsider, _role, servers = (
            server_with_members
        )
        event = servers.create_scheduled_event(
            user_id=owner.id,
            server_id=server.id,
            name="Clamp RSVP Meetup",
            start_time=int(time.time() * 1000) + 3600000,
            event_type=servers.ScheduledEventType.EXTERNAL,
            description="Clamp test event",
            location="Somewhere",
        )
        servers.rsvp_event(member_user.id, event.id, servers.RSVPStatus.GOING)
        assert len(servers.get_event_rsvps(owner.id, event.id, limit=10)) >= 1
        assert servers.get_event_rsvps(owner.id, event.id, limit=-1) == []
        assert servers.get_event_rsvps(owner.id, event.id, limit=0) == []
