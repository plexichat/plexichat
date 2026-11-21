"""Regression: a DM MESSAGE_REACTION_REMOVE_ALL must require the DM reaction intent.

``clear_reactions`` emits MESSAGE_REACTION_REMOVE_ALL with no ``guild_id``, so
for a DM channel the event carries ``server_id=None`` and a ``channel_id`` and
is classified as DM-scoped by the gateway. The guild intent map gates the
event with GUILD_MESSAGE_REACTIONS, but the DM intent map had no entry for it,
so the router's default-allow for unmapped DM events let the event reach any
connection -- including one that explicitly opted out of direct messages by
requesting only GUILD_MESSAGE_REACTIONS.
"""

import pytest

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core.events.models import Event
from src.core.events.router import get_dm_intent, get_required_intent
from src.core.events.types import EventType, GatewayIntent


DM_REACTION = int(GatewayIntent.DIRECT_MESSAGE_REACTIONS)
GUILD_REACTION = int(GatewayIntent.GUILD_MESSAGE_REACTIONS)


def _event(*, server_id, channel_id=55):
    return Event(
        event_type=EventType.MESSAGE_REACTION_REMOVE_ALL,
        data={},
        server_id=server_id,
        channel_id=channel_id,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def test_remove_all_has_both_guild_and_dm_intent_entries():
    assert get_required_intent(EventType.MESSAGE_REACTION_REMOVE_ALL) == (
        GatewayIntent.GUILD_MESSAGE_REACTIONS
    )
    assert get_dm_intent(EventType.MESSAGE_REACTION_REMOVE_ALL) == (
        GatewayIntent.DIRECT_MESSAGE_REACTIONS
    )


def test_dm_remove_all_requires_dm_reaction_intent():
    event = _event(server_id=None)

    assert filter_event_by_intents(event, DM_REACTION) is True
    # A connection that only opted into guild reactions must NOT receive a
    # DM reaction event. This is the cross-scope leak the missing map entry
    # allowed; the guild-intent fallback in the gateway gate also let it
    # through because it checked GUILD_MESSAGE_REACTIONS, not the DM intent.
    assert filter_event_by_intents(event, GUILD_REACTION) is False
    assert filter_event_by_intents(event, 0) is False


def test_guild_remove_all_still_requires_guild_reaction_intent():
    event = _event(server_id=99)

    assert filter_event_by_intents(event, GUILD_REACTION) is True
    assert filter_event_by_intents(event, DM_REACTION) is False
    assert filter_event_by_intents(event, 0) is False
