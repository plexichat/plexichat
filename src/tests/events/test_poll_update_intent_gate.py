"""POLL_UPDATE (poll activity on a message) must obey the message intent.

POLL_UPDATE was absent from BOTH gateway intent maps. ``emit_poll_update``
stamps a ``channel_id`` but no ``server_id``, so the router classifies the
event as DM-scoped and, finding no DM entry, falls through to "no intent
required". That default-allow delivered poll activity (message id, actor,
action, close state) to sockets that requested no messaging intent at all.
A poll is attached to a message, so the gate is the message intent of the
conversation: DIRECT_MESSAGES for DM scope, GUILD_MESSAGES for guild scope.
"""

import pytest

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core.events.models import Event
from src.core.events.router import (
    filter_by_intents,
    get_dm_intent,
    get_required_intent,
)
from src.core.events.types import EventType, GatewayIntent


@pytest.fixture(autouse=True)
def _fresh_intent_cache():
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def _poll_event(*, channel_id=42, server_id=None):
    """Build the POLL_UPDATE shape emitted by ``emit_poll_update``."""
    return Event(
        event_type=EventType.POLL_UPDATE,
        data={
            "poll_id": "10",
            "message_id": "99",
            "channel_id": str(channel_id),
            "action": "vote",
            "actor_id": "7",
            "is_ended": False,
        },
        channel_id=channel_id,
        server_id=server_id,
    )


def test_poll_update_is_present_in_both_intent_maps():
    assert get_required_intent(EventType.POLL_UPDATE) == GatewayIntent.GUILD_MESSAGES
    assert get_dm_intent(EventType.POLL_UPDATE) == GatewayIntent.DIRECT_MESSAGES


def test_emitted_shape_is_dm_scoped_and_requires_direct_messages():
    # The real emitter never sets server_id; with a channel id this is exactly
    # the DM-classified shape the router's DM map must gate.
    event = _poll_event(channel_id=42, server_id=None)
    assert event.server_id is None and event.channel_id is not None

    assert filter_by_intents(event, 0) is False
    assert filter_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is False
    assert filter_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is True

    # Same gate on the websocket fan-out path used for live connections.
    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is True


def test_guild_scoped_poll_update_requires_guild_messages():
    event = _poll_event(channel_id=42, server_id=9)

    assert filter_by_intents(event, 0) is False
    assert filter_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is False
    assert filter_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is True

    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is True
