"""Per-server scheduled-event capacity is enforced, not dead config.

``ScheduledEventManager._load_config`` loaded ``max_events_per_server`` (100)
but neither ``create_event`` nor ``generate_recurring_instances`` ever applied
it, so any actor with ``events.manage`` could insert unbounded scheduled-event
rows. The capacity read must also fail closed: an unreadable count must deny
creation instead of being treated as "zero events".

Run with: pytest src/tests/servers/test_scheduled_event_capacity_bound.py -q
"""

import time

import pytest

from src.core.servers.exceptions import ScheduledEventError


def _make_event(servers, server, owner, name: str):
    return servers.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name=name,
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=servers.ScheduledEventType.EXTERNAL,
        location="Test Location",
    )


@pytest.mark.servers
class TestScheduledEventCapacityBound:
    def test_create_event_enforces_per_server_cap(
        self, server_with_members, monkeypatch
    ):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        monkeypatch.setitem(servers._event_manager._config, "max_events_per_server", 2)

        assert _make_event(servers, server, owner, "first") is not None
        assert _make_event(servers, server, owner, "second") is not None

        # The third creation is rejected at capacity; nothing more is written.
        with pytest.raises(ScheduledEventError, match="Maximum 2 scheduled events"):
            _make_event(servers, server, owner, "third")

    def test_unreadable_capacity_count_denies_creation(
        self, server_with_members, monkeypatch
    ):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        event_mgr = servers._event_manager
        real_fetch_one = event_mgr._db.fetch_one

        def _count_returns_none(query, params=()):
            if "COUNT(*) AS count FROM srv_scheduled_events" in query:
                return None
            return real_fetch_one(query, params)

        monkeypatch.setattr(event_mgr._db, "fetch_one", _count_returns_none)

        with pytest.raises(ScheduledEventError, match="Cannot verify scheduled event"):
            _make_event(servers, server, owner, "blocked")

    def test_terminal_events_do_not_consume_capacity(
        self, server_with_members, monkeypatch
    ):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        monkeypatch.setitem(servers._event_manager._config, "max_events_per_server", 1)

        first = _make_event(servers, server, owner, "first")
        with pytest.raises(ScheduledEventError, match="Maximum 1 scheduled events"):
            _make_event(servers, server, owner, "second")

        # Move the first event to a terminal status; capacity frees up.
        servers.update_scheduled_event(
            user_id=owner.id,
            event_id=first.id,
            status=servers.ScheduledEventStatus.CANCELLED,
        )
        assert _make_event(servers, server, owner, "third") is not None
