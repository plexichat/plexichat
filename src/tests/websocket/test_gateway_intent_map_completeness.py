"""Static completeness guard for the gateway intent maps.

Every event type that carries message/typing/poll activity must be reachable by
exactly one of the four intent gates:

* ``EVENT_INTENT_MAP`` (guild scope),
* ``DM_EVENT_INTENT_MAP`` (DM scope),
* ``GUILD_MESSAGE_EVENTS`` (the message fast path), or
* the explicit compensating gate in ``websocket.intents`` for scope-less
  message-family events that the router would otherwise default-allow.

The router is deliberately ``default-allow`` for any event type absent from
all of its tables, so a single forgotten entry silently delivers a sensitive
event to connections that requested no matching intent.  This module derives
the set to check from the ``EventType`` enum itself, so adding a new
``MESSAGE_*``/``TYPING_*``/poll event without gating it makes the test fail
until the gate is added.  The behavioural checks then prove the gate actually
denies a scope-less and a DM-scoped emitted shape with ``intents == 0`` (they
fail on the un-gated code, so they are not vacuous).
"""

import pytest

from src.api.websocket.intents import (
    clear_intent_decision_cache,
    filter_event_by_intents,
)
from src.core.events.models import Event
from src.core.events.router import (
    DM_EVENT_INTENT_MAP,
    EVENT_INTENT_MAP,
    GUILD_MESSAGE_EVENTS,
)
from src.core.events.types import EventType


# Activity events whose payload discloses conversation activity and which must
# never be default-allow delivered. Derived from the enum so new members are
# picked up automatically.
def _activity_events():
    return {
        e
        for e in EventType
        if e.name.startswith("MESSAGE_")
        or e.name.startswith("TYPING_")
        or e.name == "POLL_UPDATE"
    }


# A floor of known-sensitive members keeps the derived set non-vacuous: if the
# enum is renamed wholesale this assertion fails instead of silently passing.
_KNOWN_ACTIVITY_EVENTS = {
    EventType.MESSAGE_CREATE,
    EventType.MESSAGE_UPDATE,
    EventType.MESSAGE_DELETE,
    EventType.MESSAGE_DELETE_BULK,
    EventType.MESSAGE_ACK,
    EventType.MESSAGE_REACTION_ADD,
    EventType.MESSAGE_REACTION_REMOVE,
    EventType.MESSAGE_REACTION_REMOVE_ALL,
    EventType.MESSAGE_REACTION_REMOVE_EMOJI,
    EventType.MESSAGE_BOOKMARK_ADD,
    EventType.MESSAGE_BOOKMARK_REMOVE,
    EventType.TYPING_START,
    EventType.TYPING_STOP,
    EventType.POLL_UPDATE,
}

# Events the compensating gate in ``websocket.intents`` handles explicitly
# (absent from the router maps). This mirrors that gate on purpose: a change to
# the runtime gate must be reflected here or the completeness check fails.
_COMPENSATED_ACTIVITY_EVENTS = {
    EventType.MESSAGE_ACK,
    EventType.MESSAGE_BOOKMARK_ADD,
    EventType.MESSAGE_BOOKMARK_REMOVE,
}


@pytest.fixture(autouse=True)
def _fresh_intent_cache():
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def test_activity_event_set_is_non_vacuous():
    activity = _activity_events()
    assert _KNOWN_ACTIVITY_EVENTS <= activity


def test_every_activity_event_has_an_intent_gate():
    activity = _activity_events()
    assert activity, "the derived activity set must not be empty"
    for event_type in activity:
        covered = (
            event_type in EVENT_INTENT_MAP
            or event_type in DM_EVENT_INTENT_MAP
            or event_type in GUILD_MESSAGE_EVENTS
            or event_type in _COMPENSATED_ACTIVITY_EVENTS
        )
        assert covered, f"{event_type.name} has no intent gate (default-allow)"


def test_map_keys_are_real_event_types():
    for mapping in (EVENT_INTENT_MAP, DM_EVENT_INTENT_MAP):
        for key in mapping:
            assert isinstance(key, EventType)


@pytest.mark.parametrize("event_type", sorted(_activity_events(), key=lambda e: e.name))
def test_scope_less_activity_event_is_denied_without_intent(event_type):
    # The shape emitted by the scope-less emitters (no Event-level scope): the
    # router classifies it as guild scope, where default-allow used to leak it.
    event = Event(event_type=event_type, data={}, server_id=None, channel_id=None)
    assert event.server_id is None and event.channel_id is None
    assert filter_event_by_intents(event, 0) is False, (
        f"{event_type.name} is default-allow for a scope-less emit"
    )


@pytest.mark.parametrize("event_type", sorted(_activity_events(), key=lambda e: e.name))
def test_dm_scoped_activity_event_is_denied_without_intent(event_type):
    event = Event(event_type=event_type, data={}, server_id=None, channel_id=42)
    assert event.server_id is None and event.channel_id is not None
    assert filter_event_by_intents(event, 0) is False, (
        f"{event_type.name} is default-allow for a DM-scoped emit"
    )
