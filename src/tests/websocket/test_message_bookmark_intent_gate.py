"""MESSAGE_BOOKMARK_ADD / MESSAGE_BOOKMARK_REMOVE must obey message intent.

The bookmark routes dispatch these events with an *explicit* recipient list
(the bookmark owner) and no Event-level scope.  They are absent from both
gateway intent maps and from ``GUILD_MESSAGE_EVENTS``, so the router classifies
them as guild events and its default-allow passes them to every connection of
that user -- including sockets that requested no messaging intent at all.
The payload carries the bookmarked message's id and content, so this is a
message-family event that must be gated like the other scope-less message
events (MESSAGE_ACK).
"""

import pytest

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


BOOKMARK_EVENTS = (EventType.MESSAGE_BOOKMARK_ADD, EventType.MESSAGE_BOOKMARK_REMOVE)


@pytest.fixture(autouse=True)
def _fresh_intent_cache():
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def _bookmark_event(
    event_type=EventType.MESSAGE_BOOKMARK_ADD, *, channel_id=None, server_id=None
):
    """Build the exact scope-less shape emitted by ``_dispatch_bookmark_event``."""
    return Event(
        event_type=event_type,
        data={
            "user_id": "7",
            "message_id": "99",
            "conversation_id": "42",
            "bookmark_id": "5",
            "label": None,
            "url": "https://example.com/m/99",
            "created_at": None,
            "message_content": "private bookmarked message body",
        },
        channel_id=channel_id,
        server_id=server_id,
    )


@pytest.mark.parametrize("event_type", BOOKMARK_EVENTS)
def test_scope_less_bookmark_withheld_without_any_message_intent(event_type):
    # This is the exact emitted shape: no Event-level scope. Before the gate it
    # was delivered even with intents == 0.
    event = _bookmark_event(event_type)
    assert event.server_id is None and event.channel_id is None

    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.GUILDS)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.MESSAGE_CONTENT)) is False


@pytest.mark.parametrize("event_type", BOOKMARK_EVENTS)
def test_scope_less_bookmark_delivered_with_guild_message_intent(event_type):
    event = _bookmark_event(event_type)
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is True


@pytest.mark.parametrize("event_type", BOOKMARK_EVENTS)
def test_dm_scoped_bookmark_requires_direct_messages_intent(event_type):
    event = _bookmark_event(event_type, channel_id=42, server_id=None)
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is False
    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is True


def test_dm_and_guild_bookmark_decisions_are_not_confused_in_cache():
    dm = _bookmark_event(channel_id=42, server_id=None)
    guild = _bookmark_event(channel_id=42, server_id=9)
    assert filter_event_by_intents(dm, int(GatewayIntent.DIRECT_MESSAGES)) is True
    assert filter_event_by_intents(guild, int(GatewayIntent.DIRECT_MESSAGES)) is False
    assert filter_event_by_intents(guild, int(GatewayIntent.GUILD_MESSAGES)) is True
