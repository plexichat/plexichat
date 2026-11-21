"""Regressions for the bounded DM participant audience-resolution cache.

``EventRouter._get_dm_participant_ids`` used to resolve a DM/group audience from
scratch on every channel-scoped event that was routed implicitly: one
``get_participant_ids`` listing plus a two-way block-set lookup. A burst of
messages/typing/reactions in the same conversation repeated the whole scan.

The resolution is now memoized in the same bounded, copy-safe, short-TTL cache
used for presence audiences. These tests pin four properties:

1. a 50-event burst collapses to one of each call (non-vacuous before/after);
2. the key is the complete identity -- a different actor, channel, source or
   router never receives another audience's cached recipients (no leak);
3. results are returned by value, so a caller mutating its list cannot corrupt
   a later reuse;
4. fail-closed empty resolutions are cached too (withhold a one-TTL window,
   never widen the audience).
"""

import pytest

import src.core.events.router as router_module
from src.core.events.router import (
    EventRouter,
    clear_dm_participant_cache,
    get_dm_participant_cache_stats,
)

CHANNEL_A = 501
CHANNEL_B = 502
ACTOR = 10
PARTICIPANTS = [10, 20, 30]


class _Messaging:
    """Participant source with a per-call counter and per-channel audiences."""

    def __init__(self, participants_by_channel=None):
        self.participants_by_channel = participants_by_channel or {}
        self.participant_calls = 0

    def get_participant_ids(self, channel_id):
        self.participant_calls += 1
        return list(self.participants_by_channel.get(channel_id, []))


class _Relationships:
    """Block source exposing the batched two-way lookup, with a call counter."""

    def __init__(self, blocked_by_id=None, *, fail=False):
        self.blocked_by_id = blocked_by_id or {}
        self.fail = fail
        self.bulk_calls = 0

    def get_all_blocked_ids_bulk(self, ids):
        self.bulk_calls += 1
        if self.fail:
            raise RuntimeError("authoritative block store unavailable")
        return {int(uid): list(self.blocked_by_id.get(int(uid), [])) for uid in ids}


class _NoCache:
    """Stand-in for the cache that never memoizes: the 'before' baseline."""

    def get(self, parts):
        return None

    def put(self, parts, value):
        pass


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_dm_participant_cache()
    yield
    clear_dm_participant_cache()


def _wire(monkeypatch, *, messaging=None, relationships=None, cache=None):
    router = EventRouter(relationships_module=relationships, messaging_module=messaging)
    monkeypatch.setattr(
        router_module,
        "_dm_participant_cache",
        cache or router_module._dm_participant_cache,
    )
    return router


# --- 1. burst collapse -------------------------------------------------------


def test_dm_burst_before_and_after_call_counts(monkeypatch):
    """50 events: one participant listing + one block lookup, not 50 of each."""
    counts = {}
    for cache in (_NoCache(), router_module._dm_participant_cache):
        messaging = _Messaging({CHANNEL_A: PARTICIPANTS})
        relationships = _Relationships()
        router = _wire(
            monkeypatch, messaging=messaging, relationships=relationships, cache=cache
        )
        expected = router._get_dm_participant_ids(CHANNEL_A, ACTOR)
        for _ in range(49):
            assert router._get_dm_participant_ids(CHANNEL_A, ACTOR) == expected
        counts[cache.__class__.__name__] = (
            messaging.participant_calls,
            relationships.bulk_calls,
        )
        assert expected == PARTICIPANTS

    # Before: 50 of each. After: exactly one of each.
    assert counts["_NoCache"] == (50, 50)
    assert counts["RecipientResolutionCache"] == (1, 1)
    stats = get_dm_participant_cache_stats()
    assert stats["hits"] >= 49
    assert stats["misses"] >= 1


# --- 2. no cross-audience leak ----------------------------------------------


def test_actor_is_part_of_the_cache_key(monkeypatch):
    """Block exclusions depend on the actor, so actors cannot share a result."""
    # 10 and 20 mutually block; each actor keeps themselves and drops the other.
    relationships = _Relationships({10: [20], 20: [10]})
    messaging = _Messaging({CHANNEL_A: PARTICIPANTS})
    router = _wire(monkeypatch, messaging=messaging, relationships=relationships)

    assert sorted(router._get_dm_participant_ids(CHANNEL_A, 10)) == [10, 30]
    assert sorted(router._get_dm_participant_ids(CHANNEL_A, 20)) == [20, 30]

    # Two distinct resolutions were performed, not one shared one.
    assert messaging.participant_calls == 2
    assert relationships.bulk_calls == 2


def test_channel_is_part_of_the_cache_key(monkeypatch):
    """A different conversation resolves its own participant list."""
    messaging = _Messaging({CHANNEL_A: [10, 20], CHANNEL_B: [40, 50]})
    relationships = _Relationships()
    router = _wire(monkeypatch, messaging=messaging, relationships=relationships)

    assert router._get_dm_participant_ids(CHANNEL_A, ACTOR) == [10, 20]
    assert router._get_dm_participant_ids(CHANNEL_B, ACTOR) == [40, 50]
    assert messaging.participant_calls == 2


def test_source_and_router_swap_force_fresh_resolution(monkeypatch):
    """Source identity and router identity are in the key."""
    first_source = _Messaging({CHANNEL_A: [10, 20]})
    relationships = _Relationships()
    first = _wire(monkeypatch, messaging=first_source, relationships=relationships)
    assert first._get_dm_participant_ids(CHANNEL_A, ACTOR) == [10, 20]

    # Same channel/actor/relationship source but a different messaging source.
    second_source = _Messaging({CHANNEL_A: [40, 50]})
    second = _wire(monkeypatch, messaging=second_source, relationships=relationships)
    assert second._get_dm_participant_ids(CHANNEL_A, ACTOR) == [40, 50]

    # A fresh router over the same source objects must also re-resolve.
    third = _wire(monkeypatch, messaging=first_source, relationships=relationships)
    assert third._get_dm_participant_ids(CHANNEL_A, ACTOR) == [10, 20]
    assert first_source.participant_calls == 2


# --- 3. copy-safety ----------------------------------------------------------


def test_cached_value_is_returned_by_value(monkeypatch):
    """Mutating a returned list cannot poison the cached audience."""
    messaging = _Messaging({CHANNEL_A: PARTICIPANTS})
    router = _wire(monkeypatch, messaging=messaging, relationships=_Relationships())

    first = router._get_dm_participant_ids(CHANNEL_A, ACTOR)
    first.append(9999)
    first.reverse()

    second = router._get_dm_participant_ids(CHANNEL_A, ACTOR)
    assert second == PARTICIPANTS
    assert second is not first
    assert messaging.participant_calls == 1


# --- 4. fail-closed results are cached --------------------------------------


def test_fail_closed_empty_result_is_cached(monkeypatch):
    """A failing block lookup withholds the audience and caches the empty list."""
    messaging = _Messaging({CHANNEL_A: PARTICIPANTS})
    relationships = _Relationships(fail=True)
    router = _wire(monkeypatch, messaging=messaging, relationships=relationships)

    assert router._get_dm_participant_ids(CHANNEL_A, ACTOR) == []
    assert router._get_dm_participant_ids(CHANNEL_A, ACTOR) == []
    assert messaging.participant_calls == 1
    assert relationships.bulk_calls == 1
