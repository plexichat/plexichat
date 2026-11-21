"""Regressions for the bounded message-fanout member-list cache.

``broadcast._resolve_recipients`` used to re-issue the server-member query
(``SELECT user_id FROM srv_members WHERE server_id = ?``) for every single
``MESSAGE_CREATE`` / ``MESSAGE_UPDATE`` / ``MESSAGE_DELETE`` broadcast. On a
busy channel a burst of sends repeated that identical query over and over even
though the roster only changes on a membership write.

The query is now memoized in a bounded, short-TTL cache keyed by the audience
source and server id, and invalidated on every membership mutation. These
tests pin four properties:

1. a burst performs one member query, not one per event (measured reduction);
2. different servers / audience sources never share a cached roster (no
   cross-audience leak);
3. a membership write invalidates the roster immediately (no removed member
   keeps receiving events, no new member is left out);
4. the ``channels.view`` permission walk still runs on every broadcast, so
   caching the roster never weakens the restricted-channel filter.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import src.core.bounded_lru as bounded_lru_module
from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients
from src.core.servers.manager import channel_audience_cache as cachemod
from src.core.servers.manager.channel_audience_cache import (
    CHANNEL_AUDIENCE_MAX_ENTRIES,
    channel_audience_cache_stats,
    get_cached_channel_members,
    invalidate_channel_audience_cache,
)

SERVER = 700
OTHER_SERVER = 701
CHANNEL = 99
ACTOR = 1


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_channel_audience_cache()
    yield
    invalidate_channel_audience_cache()


class _Servers:
    """Server source with per-call counters and a mutable member roster."""

    def __init__(self, members, *, viewers=None, server_id=SERVER):
        self.members = list(members)
        self.viewers = set(viewers if viewers is not None else members)
        self.server_id = server_id
        self.member_calls = 0
        self.perm_calls = 0
        self.channel_calls = 0

    def get_channel(self, channel_id, actor_id):
        self.channel_calls += 1
        return SimpleNamespace(server_id=self.server_id)

    def get_member_user_ids(self, sid):
        self.member_calls += 1
        return list(self.members)

    def has_permission(self, uid, sid, permission, channel_id):
        self.perm_calls += 1
        return uid in self.viewers


# --- measured reduction ------------------------------------------------------


def _freeze_roster_clock(monkeypatch):
    """Pin the clock the bounded LRU reads for its TTL.

    The roster TTL is 10s, and on a loaded shard these bursts can take longer
    than that: the cache would expire mid-burst -- doing exactly its job, not
    showing a defect -- and the call counts would measure the shard's speed.
    Freeze only the reference inside the LRU module, not the stdlib module.
    """
    frozen = time.monotonic()
    monkeypatch.setattr(
        bounded_lru_module, "time", SimpleNamespace(monotonic=lambda: frozen)
    )


def test_burst_reuses_member_list(monkeypatch):
    _freeze_roster_clock(monkeypatch)
    servers = _Servers([1, 2, 3], viewers={2, 3})
    before = channel_audience_cache_stats()

    for _ in range(50):
        ids, sid = _resolve_recipients(servers, None, CHANNEL, CHANNEL, ACTOR)
        assert ids == [2, 3]
        assert sid == SERVER

    # One member query for the whole burst; the permission walk still runs per
    # event (50 * 3) so channel visibility is never memoized.
    assert servers.member_calls == 1
    assert servers.channel_calls == 50
    assert servers.perm_calls == 50 * 3
    # The counters are process-lifetime totals: other tests in the same shard
    # may already have read this cache, so measure this burst's delta.
    after = channel_audience_cache_stats()
    assert after["hits"] - before["hits"] == 49
    assert after["misses"] - before["misses"] == 1


def test_perf_before_and_after_call_counts(monkeypatch):
    """A 100-message burst on a 500-member channel: 100 queries -> 1."""
    _freeze_roster_clock(monkeypatch)
    events, members = 100, 500

    class _NoCache:
        def get(self, key, default=None):
            return default

        def set(self, key, value):
            pass

        def clear(self):
            pass

    counts = {}
    for label, cache in (
        ("before", _NoCache()),
        ("after", cachemod._channel_audience_cache),
    ):
        invalidate_channel_audience_cache()
        servers = _Servers(list(range(1, members + 1)))
        original = cachemod._channel_audience_cache
        cachemod._channel_audience_cache = cache
        try:
            for _ in range(events):
                _resolve_recipients(servers, None, CHANNEL, CHANNEL, ACTOR)
        finally:
            cachemod._channel_audience_cache = original
        counts[label] = servers.member_calls

    # Before: every message re-ran the member query. After: one query totals.
    assert counts["before"] == events
    assert counts["after"] == 1


# --- no cross-audience leak --------------------------------------------------


def test_different_servers_never_share_a_roster():
    first = _Servers([1, 2], server_id=SERVER)
    second = _Servers([3, 4], server_id=OTHER_SERVER)

    assert get_cached_channel_members(first, SERVER) == [1, 2]
    assert get_cached_channel_members(second, OTHER_SERVER) == [3, 4]
    assert get_cached_channel_members(first, SERVER) == [1, 2]

    assert first.member_calls == 1
    assert second.member_calls == 1


def test_swapped_audience_source_recomputes_instead_of_replaying():
    """A re-setup of the servers module (new identity) forces a fresh read."""
    first = _Servers([1, 2])
    second = _Servers([3, 4])

    assert get_cached_channel_members(first, SERVER) == [1, 2]
    assert get_cached_channel_members(second, SERVER) == [3, 4]
    assert second.member_calls == 1


def test_cached_roster_is_returned_by_value():
    servers = _Servers([1, 2, 3])

    first = get_cached_channel_members(servers, SERVER)
    first.append(999)
    assert get_cached_channel_members(servers, SERVER) == [1, 2, 3]


# --- invalidation on membership change ---------------------------------------


def test_membership_change_invalidates_cached_roster():
    """The shared membership funnel clears the fanout roster immediately."""
    from src.core.servers.manager.member_count_cache import (
        invalidate_member_count_cache,
    )

    servers = _Servers([1, 2, 3])

    assert get_cached_channel_members(servers, SERVER) == [1, 2, 3]

    # A member is removed (and the manager's shared invalidation funnel fires).
    servers.members = [1, 2]
    invalidate_member_count_cache()

    assert get_cached_channel_members(servers, SERVER) == [1, 2]
    assert servers.member_calls == 2


def test_direct_invalidation_drops_every_server():
    first = _Servers([1], server_id=SERVER)
    second = _Servers([2], server_id=OTHER_SERVER)
    get_cached_channel_members(first, SERVER)
    get_cached_channel_members(second, OTHER_SERVER)

    invalidate_channel_audience_cache()

    assert get_cached_channel_members(first, SERVER) == [1]
    assert get_cached_channel_members(second, OTHER_SERVER) == [2]
    assert first.member_calls == 2
    assert second.member_calls == 2


# --- bounded and fail-closed --------------------------------------------------


def test_cache_is_bounded():
    servers = _Servers([1])

    for sid in range(CHANNEL_AUDIENCE_MAX_ENTRIES + 50):
        get_cached_channel_members(servers, sid)

    stats = channel_audience_cache_stats()
    assert stats["size"] <= CHANNEL_AUDIENCE_MAX_ENTRIES
    assert stats["bytes"] <= stats["max_bytes"]
    assert stats["evictions"] > 0


def test_failed_member_query_caches_empty_fail_closed():
    class _Broken:
        def get_member_user_ids(self, sid):
            raise RuntimeError("db unavailable")

    broken = _Broken()
    assert get_cached_channel_members(broken, SERVER) == []
    assert get_cached_channel_members(broken, SERVER) == []


# --- permission filtering is never memoized ----------------------------------


def test_permission_change_takes_effect_without_invalidation():
    servers = _Servers([1, 2, 3], viewers={2})

    assert _resolve_recipients(servers, None, CHANNEL, CHANNEL, ACTOR)[0] == [2]

    # Grant access to user 3. The roster cache is untouched, but the
    # per-broadcast channels.view walk must pick the change up immediately.
    servers.viewers.add(3)
    assert _resolve_recipients(servers, None, CHANNEL, CHANNEL, ACTOR)[0] == [2, 3]
    assert servers.member_calls == 1
