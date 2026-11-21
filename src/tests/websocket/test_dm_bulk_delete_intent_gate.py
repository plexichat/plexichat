"""Regression: DM MESSAGE_DELETE_BULK must honor the DIRECT_MESSAGES intent.

MESSAGE_DELETE_BULK is the only message-family event that was missing from the
DM intent map. ``emit_message_delete_bulk`` emits it with ``server_id=None``
for DM channels, so the intent router classified it as a DM event, found no DM
intent entry, and delivered it unconditionally -- even to a client that never
requested DIRECT_MESSAGES. Every sibling (MESSAGE_CREATE/UPDATE/DELETE and the
reaction family) is gated in both scopes; bulk delete must be too.

The frame leaks the ids of messages deleted in a private conversation (and the
timing of the deletion) to a participant that explicitly opted out of DM
events. A participant can trigger it at will by bulk-deleting their own
messages, so this is attacker-reachable at the gateway boundary.
"""

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core import events
from src.core.events.types import GatewayIntent


def _dm_event(event_type):
    """A DM-scoped event as emitted by the gateway payload builders."""
    return events.Event(
        event_type=event_type,
        data={"channel_id": "999", "message_ids": ["1", "2"]},
        server_id=None,
        channel_id=999,
    )


def test_dm_bulk_delete_requires_direct_messages_intent():
    clear_intent_decision_cache()

    without_dm = int(GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES)
    with_dm = int(without_dm | GatewayIntent.DIRECT_MESSAGES)

    # The single-message twin establishes the expected gate.
    assert (
        filter_event_by_intents(_dm_event(events.EventType.MESSAGE_DELETE), without_dm)
        is False
    )
    assert (
        filter_event_by_intents(_dm_event(events.EventType.MESSAGE_DELETE), with_dm)
        is True
    )

    # Bulk delete must behave identically in DM scope.
    assert (
        filter_event_by_intents(
            _dm_event(events.EventType.MESSAGE_DELETE_BULK), without_dm
        )
        is False
    )
    assert (
        filter_event_by_intents(
            _dm_event(events.EventType.MESSAGE_DELETE_BULK), with_dm
        )
        is True
    )


def test_guild_bulk_delete_still_requires_guild_messages_intent():
    """Adding the DM mapping must not weaken the existing guild gate."""
    clear_intent_decision_cache()

    event = events.Event(
        event_type=events.EventType.MESSAGE_DELETE_BULK,
        data={"channel_id": "999", "guild_id": "7"},
        server_id=7,
        channel_id=999,
    )
    assert filter_event_by_intents(event, int(GatewayIntent.GUILDS)) is False
    assert (
        filter_event_by_intents(
            event, int(GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES)
        )
        is True
    )
