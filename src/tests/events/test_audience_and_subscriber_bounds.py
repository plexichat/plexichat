"""Regressions for two event-dispatch weaknesses.

1. A server-scoped event must never fall back to DM-participant routing
   when the authoritative server-membership module is unavailable — that
   silently rerouted guild content to an unrelated conversation audience.
2. The critical (non-lossy) subscriber pool must be bounded: it is exempt
   from both idle-TTL pruning and merged-cap eviction, so without its own
   ceiling a repeated registration loop grows the maps without limit.
"""

from unittest.mock import MagicMock

import pytest

from src.core.events.manager import EventManager
from src.core.events.models import Event
from src.core.events.router import EventRouter
from src.core.events.types import EventType


pytestmark = pytest.mark.unit


# --- fail-closed audience resolution ---------------------------------------


def test_server_scoped_event_never_uses_dm_participants_without_servers():
    """A server event cannot leak to a DM audience via the old ``elif``.

    With no servers module wired, the previous code hit the DM branch and
    handed the event to every participant of the co-listed channel.
    """
    messaging = MagicMock()
    messaging.get_participant_ids.return_value = [11, 22]
    router = EventRouter(servers_module=None, messaging_module=messaging)

    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        server_id=900,
        channel_id=500,
        user_id=7,
    )
    assert router.get_recipients(event) == []
    messaging.get_participant_ids.assert_not_called()


def test_server_scoped_event_still_uses_server_members():
    """The fix must not weaken normal guild-member routing."""
    servers = MagicMock()
    servers.get_member_user_ids.return_value = [1, 2]
    messaging = MagicMock()
    messaging.get_participant_ids.return_value = [11, 22]
    router = EventRouter(servers_module=servers, messaging_module=messaging)

    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        server_id=900,
        channel_id=500,
        user_id=7,
    )
    assert sorted(router.get_recipients(event)) == [1, 2]
    messaging.get_participant_ids.assert_not_called()


def test_dm_event_still_uses_conversation_participants():
    """A genuinely DM-scoped event keeps its normal routing path."""
    messaging = MagicMock()
    messaging.get_participant_ids.return_value = [11, 22]
    router = EventRouter(messaging_module=messaging)

    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        channel_id=500,
        user_id=7,
    )
    assert sorted(router.get_recipients(event)) == [11, 22]


# --- bounded critical subscriber pool --------------------------------------


def test_critical_subscriber_pool_has_hard_ceiling():
    """Repeated critical registrations cannot grow the maps without bound."""
    em = EventManager()
    em._MAX_CRITICAL_SUBSCRIBERS = 3
    callbacks = [MagicMock(name=f"crit-{i}") for i in range(6)]

    for cb in callbacks:
        em.subscribe(cb, critical=True)

    assert len(em._critical_subscribers) == 3
    assert len(em._last_used) == 3
    for cb in callbacks[3:]:
        assert cb not in em._critical_subscribers
        assert cb not in em._subscribers
        assert cb not in em._last_used

    # A saturated critical lane must not block ordinary subscribers.
    regular = MagicMock(name="regular")
    em.subscribe(regular)
    assert regular in em._subscribers


def test_rejected_critical_subscriber_receives_no_events():
    """A refused critical subscriber is not silently delivered to either."""
    em = EventManager()
    em._MAX_CRITICAL_SUBSCRIBERS = 1
    accepted, refused = MagicMock(name="accepted"), MagicMock(name="refused")
    em.subscribe(accepted, critical=True)
    em.subscribe(refused, critical=True)

    em._router.get_recipients = MagicMock(return_value=[101])
    em._publish_cross_worker = MagicMock()

    em.dispatch(Event(event_type=EventType.READY, data={}), user_ids=[101])

    accepted.assert_called_once()
    refused.assert_not_called()
