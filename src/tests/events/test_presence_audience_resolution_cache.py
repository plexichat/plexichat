"""Regressions for the bounded presence-audience resolution cache.

``EventRouter._get_presence_recipients`` used to rebuild a presence update's
whole audience on every event: the subject's friend graph, the full member list
of every server they belong to, and a block filter. Presence is one of the
most frequent gateway events, so a burst for one user repeated that walk (and
its per-server member lookups) for every frame.

The resolution is now memoized in a bounded, short-TTL cache keyed by the
complete resolution identity. These tests pin three properties:

1. identical resolutions are reused (perf: the walk runs once per burst);
2. the key is the complete identity -- a different subject, source or router
   never receives another subject's cached audience (no cross-audience leak);
3. clearing the cache forces a fresh resolution.
"""

import pytest

import src.core.events.router as router_module
from src.core.events.models import Event
from src.core.events.recipient_cache import RecipientResolutionCache
from src.core.events.router import (
    EventRouter,
    clear_presence_audience_cache,
    get_presence_audience_cache_stats,
)
from src.core.events.types import EventType

SUBJECT = 7


class _Relationships:
    """Relationship source with per-call counters and per-user audiences."""

    def __init__(self, *, friends=None, blocked=None):
        self.friends = friends or {}
        self.blocked = blocked or {}
        self.friend_calls = 0
        self.block_calls = 0

    def get_friend_ids(self, user_id):
        self.friend_calls += 1
        return list(self.friends.get(user_id, []))

    def get_all_blocked_ids(self, user_id):
        self.block_calls += 1
        return list(self.blocked.get(user_id, []))


class _Servers:
    """Server source with per-call counters and per-user/per-server audiences."""

    def __init__(self, *, servers_by_user=None, members=None):
        self.servers_by_user = servers_by_user or {}
        self.members = members or {}
        self.user_server_calls = 0
        self.member_calls = 0

    def get_user_server_ids(self, user_id):
        self.user_server_calls += 1
        return list(self.servers_by_user.get(user_id, []))

    def get_member_user_ids(self, server_id):
        self.member_calls += 1
        return list(self.members.get(server_id, []))


class _NoCache:
    """Stand-in for the cache that never memoizes: the 'before' baseline."""

    def get(self, parts):
        return None

    def put(self, parts, value):
        pass


def _presence_event(user_id):
    return Event(
        event_type=EventType.PRESENCE_UPDATE,
        data={"status": "online"},
        user_id=user_id,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_presence_audience_cache()
    yield
    clear_presence_audience_cache()


# --- identical resolutions are reused ----------------------------------------


def test_presence_burst_before_and_after_call_counts():
    """A 100-event burst: one friend/server walk, not 100 walks."""
    events = 100
    counts = {}
    original = router_module._presence_audience_cache
    try:
        for label, cache in (
            ("before", _NoCache()),
            ("after", original),
        ):
            rel = _Relationships(friends={SUBJECT: [2, 3]}, blocked={SUBJECT: [3]})
            srv = _Servers(
                servers_by_user={SUBJECT: [10, 11]},
                members={10: [2, 3, 4], 11: [2, 5]},
            )
            router = EventRouter(relationships_module=rel, servers_module=srv)
            router_module._presence_audience_cache = cache
            event = _presence_event(SUBJECT)
            for _ in range(events):
                assert sorted(router.get_recipients(event)) == [2, 4, 5]
            counts[label] = (
                rel.friend_calls,
                srv.user_server_calls,
                srv.member_calls,
                rel.block_calls,
            )
    finally:
        router_module._presence_audience_cache = original

    # before: every event re-resolves the audience (two server member walks).
    assert counts["before"] == (events, events, events * 2, events)
    # after: one friend lookup, one server list, two member walks, one block.
    assert counts["after"] == (1, 1, 2, 1)


def test_stats_report_hits_and_misses(monkeypatch):
    rel = _Relationships(friends={SUBJECT: [2]})
    srv = _Servers()
    router = EventRouter(relationships_module=rel, servers_module=srv)
    event = _presence_event(SUBJECT)

    router.get_recipients(event)
    for _ in range(5):
        router.get_recipients(event)

    stats = get_presence_audience_cache_stats()
    assert (stats["hits"], stats["misses"]) == (5, 1)


# --- no cross-audience leak ---------------------------------------------------


def test_different_subjects_never_share_a_cached_audience():
    rel = _Relationships(friends={SUBJECT: [2, 3], 8: [4, 5]})
    srv = _Servers()
    router = EventRouter(relationships_module=rel, servers_module=srv)

    assert sorted(router.get_recipients(_presence_event(SUBJECT))) == [2, 3]
    assert sorted(router.get_recipients(_presence_event(8))) == [4, 5]
    assert sorted(router.get_recipients(_presence_event(SUBJECT))) == [2, 3]

    stats = get_presence_audience_cache_stats()
    assert (stats["hits"], stats["misses"]) == (1, 2)


def test_swapped_source_recomputes_instead_of_replaying():
    """A new source object identity forces a fresh walk."""
    first = _Relationships(friends={SUBJECT: [2]})
    second = _Relationships(friends={SUBJECT: [3]})
    srv = _Servers()
    router = EventRouter(relationships_module=first, servers_module=srv)
    event = _presence_event(SUBJECT)

    assert sorted(router.get_recipients(event)) == [2]
    router._relationships = second
    assert sorted(router.get_recipients(event)) == [3]


def test_different_routers_do_not_share_an_audience():
    """A fresh router (as produced by events.setup) gets a fresh namespace."""
    rel = _Relationships(friends={SUBJECT: [2]})
    srv = _Servers()
    first = EventRouter(relationships_module=rel, servers_module=srv)
    second = EventRouter(relationships_module=rel, servers_module=srv)
    event = _presence_event(SUBJECT)

    assert sorted(first.get_recipients(event)) == [2]
    rel.friends[SUBJECT] = [3]
    assert sorted(second.get_recipients(event)) == [3]


# --- invalidation / primitive behaviour --------------------------------------


def test_clear_forces_a_fresh_resolution():
    rel = _Relationships(friends={SUBJECT: [2]})
    srv = _Servers()
    router = EventRouter(relationships_module=rel, servers_module=srv)
    event = _presence_event(SUBJECT)

    assert sorted(router.get_recipients(event)) == [2]
    rel.friends[SUBJECT] = [3]
    # Cached copy is served until the cache is explicitly invalidated.
    assert sorted(router.get_recipients(event)) == [2]
    clear_presence_audience_cache()
    assert sorted(router.get_recipients(event)) == [3]


def test_underlying_cache_is_bounded_and_copy_safe():
    """The presence cache uses the shared bounded, copy-safe primitive."""
    cache = RecipientResolutionCache(max_entries=2, ttl_seconds=100, clock=lambda: 0.0)
    for key in range(5):
        cache.put((key,), [key])
    assert cache.stats()["size"] == 2

    cache.put((9,), [9, 10])
    first = cache.get((9,))
    assert first == [9, 10]
    first.append(999)  # caller mutation must not corrupt the cached value
    assert cache.get((9,)) == [9, 10]
