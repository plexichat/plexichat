"""Regression tests for the bounded gateway intent-decision fan-out cache.

The intent gate is the audience boundary for gateway events: deciding ``True``
for the wrong scope/intent combination leaks an event to a client that never
asked for it. These tests pin three properties at once:

* identical recipients share one decision (fan-out cost collapses),
* a different audience (DM vs. guild) never reuses that decision, and
* the memo is bounded and can be invalidated in O(1).
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

import src.api.websocket.intents as intents_mod
from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.intent_decision_cache import IntentDecisionCache
from src.api.websocket.intents import (
    ALL_INTENTS,
    GatewayIntent,
    clear_intent_decision_cache,
    filter_event_by_intents,
    get_intent_decision_cache_stats,
)
from src.core.events.models import Event
from src.core.events.types import EventType


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Start every test from a clean decision cache."""
    clear_intent_decision_cache()
    yield
    clear_intent_decision_cache()


def _event(*, event_type=EventType.MESSAGE_CREATE, server_id=10, channel_id=20):
    return Event(
        event_type=event_type,
        data={"message_id": 1},
        server_id=server_id,
        channel_id=channel_id,
    )


def test_identical_decision_is_computed_once(monkeypatch):
    calls = []
    real = intents_mod._filter_by_intents

    def counting(event, intents):
        calls.append((event.event_type, intents))
        return real(event, intents)

    monkeypatch.setattr(intents_mod, "_filter_by_intents", counting)

    event = _event()
    mask = int(GatewayIntent.GUILD_MESSAGES)
    results = [filter_event_by_intents(event, mask) for _ in range(200)]

    assert all(r is True for r in results)
    assert len(calls) == 1, "200 identical recipients must pay one intent gate"
    stats = get_intent_decision_cache_stats()
    assert stats["hits"] == 199
    assert stats["misses"] == 1
    assert stats["size"] == 1


def test_no_cross_audience_leak_between_dm_and_guild(monkeypatch):
    """The same type + intent mask is a different decision for DM vs. guild."""
    calls = []
    real = intents_mod._filter_by_intents

    def counting(event, intents):
        calls.append(intents)
        return real(event, intents)

    monkeypatch.setattr(intents_mod, "_filter_by_intents", counting)

    dm_typing = int(GatewayIntent.DIRECT_MESSAGE_TYPING)
    dm = _event(event_type=EventType.TYPING_START, server_id=None, channel_id=55)
    guild = _event(event_type=EventType.TYPING_START, server_id=99, channel_id=55)

    # A DM-typing-only client gets DM typing but NOT guild typing.
    assert filter_event_by_intents(dm, dm_typing) is True
    assert filter_event_by_intents(guild, dm_typing) is False
    # ...and a guild-typing-only client gets the opposite pair. These reuse
    # the cached decisions (dict lookup), never recomputing/widening them.
    guild_typing = int(GatewayIntent.GUILD_MESSAGE_TYPING)
    assert filter_event_by_intents(dm, guild_typing) is False
    assert filter_event_by_intents(guild, guild_typing) is True

    # Four distinct (scope, intent) decisions -> four evaluations. A single
    # evaluation would prove the scope was omitted from the cache key.
    assert len(calls) == 4
    assert get_intent_decision_cache_stats()["size"] == 4

    # Repeating any of them returns the cached, per-audience answer.
    assert filter_event_by_intents(guild, dm_typing) is False
    assert filter_event_by_intents(dm, dm_typing) is True
    assert len(calls) == 4


def test_invalid_masks_never_reach_the_cache_or_the_gate(monkeypatch):
    calls = []

    def forbidden(event, intents):
        calls.append(intents)
        return True

    monkeypatch.setattr(intents_mod, "_filter_by_intents", forbidden)

    for bad in (-1, True, ALL_INTENTS + 1, "513", None):
        assert filter_event_by_intents(_event(), cast(int, bad)) is False

    assert calls == []
    assert get_intent_decision_cache_stats()["size"] == 0


def test_burst_fanout_collapses_per_recipient_filter_calls(monkeypatch):
    """End-to-end: 200 recipients share one decision on the wire."""
    calls = []
    real = intents_mod._filter_by_intents

    def counting(event, intents):
        calls.append(intents)
        return real(event, intents)

    monkeypatch.setattr(intents_mod, "_filter_by_intents", counting)

    class _SessionManager:
        def __init__(self, connections):
            self.connections = connections

        def get_connections_for_users(self, user_ids):
            return list(self.connections)

        def record_event(self, session_id, event):
            pass

    connections = []
    for i in range(200):
        conn = Connection(
            websocket=AsyncMock(), connection_id=f"c{i}", intents=ALL_INTENTS
        )
        conn.state = ConnectionState.READY
        conn.user_id = 123
        conn.is_selftest = True
        connections.append(conn)

    dispatcher = GatewayDispatcher(cast(object, _SessionManager(connections)))

    async def _run():
        return await dispatcher.dispatch_event(_event(), [123])

    import asyncio

    sent = asyncio.run(_run())

    assert sent == 200
    assert len(calls) == 1, "one gate evaluation for the whole fan-out"


def test_cache_is_bounded_and_clear_bumps_epoch():
    cache = IntentDecisionCache(max_entries=4)
    for i in range(100):
        cache.put(EventType.MESSAGE_CREATE, False, 1000 + i, True)

    stats = cache.stats()
    assert stats["size"] == 4
    assert stats["max_entries"] == 4

    # A false decision is cached and returned as False (not treated as a miss).
    cache.put(EventType.MESSAGE_CREATE, True, 7, False)
    assert cache.get(EventType.MESSAGE_CREATE, True, 7) is False
    assert cache.stats()["hits"] == 1
    assert cache.get(EventType.MESSAGE_CREATE, False, 1234) is None
    assert cache.stats()["misses"] == 1

    epoch = cache.stats()["epoch"]
    cache.clear()
    after = cache.stats()
    assert after["size"] == 0
    assert after["hits"] == 0
    assert after["misses"] == 0
    assert after["epoch"] == epoch + 1
    assert cache.get(EventType.MESSAGE_CREATE, True, 7) is None
