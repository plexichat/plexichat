"""MESSAGE_ACK (read receipts) must obey the message intent of its scope.

MESSAGE_ACK is the one message-family event that is missing from both gateway
intent maps and from ``GUILD_MESSAGE_EVENTS``. Without an explicit gate the
router's default-allow delivered a read receipt -- which discloses another
user's activity in a conversation -- to every connection, including sockets
that never requested any messaging intent.
"""

import pytest

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


@pytest.fixture(autouse=True)
def _fresh_intent_cache():
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def _ack_event(*, channel_id=None, server_id=None):
    """Build a MESSAGE_ACK the way the read-receipt routes do (scope-less by default)."""
    return Event(
        event_type=EventType.MESSAGE_ACK,
        data={
            "channel_id": "42",
            "user_id": "7",
            "message_id": "99",
        },
        channel_id=channel_id,
        server_id=server_id,
    )


def test_scope_less_ack_withheld_without_any_message_intent():
    # This is the exact shape emitted by the read-receipt routes: no Event-level
    # scope. Before the gate it was delivered even with intents == 0.
    event = _ack_event()
    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.GUILDS)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is False


def test_scope_less_ack_delivered_with_guild_message_intent():
    event = _ack_event()
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is True


def test_dm_scoped_ack_requires_direct_messages_intent():
    event = _ack_event(channel_id=42, server_id=None)
    # A DM read receipt must not be delivered just because the socket asked for
    # guild messages (nor with no intents at all).
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is False
    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGES)) is True


def test_dm_and_guild_ack_decisions_are_not_confused_in_cache():
    dm = _ack_event(channel_id=42, server_id=None)
    guild = _ack_event(channel_id=42, server_id=9)
    # Same event type, same mask, different scope -> different decision. This
    # also proves the gate runs before the memo answer is reused.
    assert filter_event_by_intents(dm, int(GatewayIntent.DIRECT_MESSAGES)) is True
    assert filter_event_by_intents(guild, int(GatewayIntent.DIRECT_MESSAGES)) is False
    assert filter_event_by_intents(guild, int(GatewayIntent.GUILD_MESSAGES)) is True
