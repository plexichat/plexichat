"""Regressions for the bounded, short-TTL typing-audience cache.

``_get_channel_recipients`` used to resolve a channel's typing audience on
every frame: list every server member and run a ``channels.view`` permission
check for each. The F12 cache wrapped that result but keyed it only by
(server, channel) while the value excluded one particular sender — so two
senders in the same channel shared an entry baked with the *first* sender's
exclusion. That both wasted the second sender's audience and leaked the first
sender's identity into it.

These tests pin the hardened contract:

* unchanged behaviour — recipients are the channel's viewers minus the sender;
* a burst pays for a single member sweep (measured before/after);
* no cross-user leak — the cached value is sender-agnostic, so one sender's
  audience is never served to another;
* invalidation on a presence change drops warm entries;
* the audience map is bounded (LRU) and expires on a short TTL.

Only counting fakes are used — the real route helpers, key builder and cache
stay on the path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.api as api
import src.api.websocket.handlers.presence as hp
from src.core.presence.typing_audience import TypingAudienceCache

CHANNEL = 333
GUILD = 222


class _Servers:
    """Server source with per-call counters and a per-channel viewer set."""

    def __init__(self, members, viewers_by_channel=None):
        self.members = list(members)
        self.viewers_by_channel = viewers_by_channel or {}
        self.member_calls = 0
        self.perm_calls = 0
        self.channel_calls = 0

    def get_channel(self, cid, actor_id):
        self.channel_calls += 1
        return SimpleNamespace(server_id=GUILD)

    def get_member_user_ids(self, sid, exclude_user_id=None):
        self.member_calls += 1
        ids = list(self.members)
        if exclude_user_id is not None:
            ids = [i for i in ids if i != exclude_user_id]
        return ids

    def has_permission(self, uid, sid, permission, cid):
        self.perm_calls += 1
        return uid in self.viewers_by_channel.get(cid, set())


def _wire(monkeypatch, servers, messaging=None):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    hp._typing_audience_cache.clear()
    return hp._get_channel_recipients


# --- unchanged behaviour + query reduction -----------------------------------


def test_recipients_are_viewers_minus_sender(monkeypatch):
    servers = _Servers(
        members=[1, 2, 3, 4, 5],
        viewers_by_channel={CHANNEL: {1, 2, 3, 4}},
    )
    resolve = _wire(monkeypatch, servers)

    recipients, server_id = resolve(1, CHANNEL)

    assert server_id == GUILD
    # Sender 1 excluded, non-viewer 5 excluded.
    assert recipients == [2, 3, 4]
    assert 1 not in recipients
    assert 5 not in recipients


def test_burst_call_counts_before_and_after(monkeypatch):
    """100 events from 100 different senders: one walk, not 100 walks."""

    class _NoCache:
        def get(self, *_):
            return None

        def put(self, *_):
            pass

    events = 100
    members = list(range(1, 501))
    counts = {}
    real_cache = hp._typing_audience_cache
    for label, cache in (("before", _NoCache()), ("after", real_cache)):
        servers = _Servers(
            members=members,
            viewers_by_channel={CHANNEL: set(members)},
        )
        resolve = _wire(monkeypatch, servers)
        hp._typing_audience_cache = cache
        try:
            for sender in range(1, events + 1):
                resolve(sender, CHANNEL)
        finally:
            hp._typing_audience_cache = real_cache
        counts[label] = (
            servers.channel_calls,
            servers.member_calls,
            servers.perm_calls,
        )

    # Before: every event re-lists members and re-checks all 500 permissions.
    assert counts["before"] == (events, events, events * len(members))
    # After: the channel lookup happens per event (access check), but the
    # member listing and permission sweep run once for the whole burst — and
    # stay correct across 100 different senders.
    assert counts["after"] == (events, 1, len(members))


# --- no cross-user leak -------------------------------------------------------


def test_two_senders_share_entry_without_cross_user_leak(monkeypatch):
    """The cached set is sender-agnostic; exclusion is applied per read."""
    servers = _Servers(
        members=[1, 2, 3],
        viewers_by_channel={CHANNEL: {1, 2, 3}},  # everyone can view
    )
    resolve = _wire(monkeypatch, servers)

    alice = resolve(1, CHANNEL)[0]
    bob = resolve(2, CHANNEL)[0]
    alice_again = resolve(1, CHANNEL)[0]

    # Old code would return [2, 3] for Bob (Alice's cached audience), leaking
    # Alice's exclusion and even delivering Bob his own typing frame.
    assert alice == [2, 3]
    assert bob == [1, 3]
    assert alice_again == [2, 3]
    assert 2 not in bob  # sender never receives own frame
    assert 1 in bob  # other sender is still a legitimate recipient
    # One member sweep across all three calls: the entry is safely shared.
    assert servers.member_calls == 1


def test_different_channels_never_share_an_entry(monkeypatch):
    servers = _Servers(
        members=[1, 2, 3, 4],
        viewers_by_channel={10: {2, 3}, 20: {3, 4}},
    )
    resolve = _wire(monkeypatch, servers)

    assert resolve(1, 10) == ([2, 3], GUILD)
    assert resolve(1, 20) == ([3, 4], GUILD)
    assert resolve(1, 10) == ([2, 3], GUILD)
    assert servers.member_calls == 2
    assert hp._typing_audience_cache.stats()["hits"] == 1


# --- invalidation on presence change ------------------------------------------


def test_invalidation_drops_warm_entry(monkeypatch):
    servers = _Servers(
        members=[1, 2, 3],
        viewers_by_channel={CHANNEL: {1, 2, 3}},
    )
    resolve = _wire(monkeypatch, servers)

    assert resolve(1, CHANNEL)[0] == [2, 3]
    assert servers.member_calls == 1

    # A presence/membership change drops the warm resolution.
    removed = hp.invalidate_typing_audience_cache(server_id=GUILD, channel_id=CHANNEL)
    assert removed == 1

    # Simulate the membership change in the source; the next read must be cold.
    servers.viewers_by_channel[CHANNEL] = {1, 2}
    assert resolve(1, CHANNEL)[0] == [2]
    assert servers.member_calls == 2


def test_full_invalidation_and_scoped_invalidation(monkeypatch):
    servers = _Servers(
        members=[1, 2, 3],
        viewers_by_channel={10: {1, 2}, 20: {1, 3}},
    )
    resolve = _wire(monkeypatch, servers)

    resolve(1, 10)
    resolve(1, 20)
    assert hp._typing_audience_cache.stats()["size"] == 2

    # Channel-scoped invalidation leaves the other channel warm.
    assert hp.invalidate_typing_audience_cache(channel_id=10) == 1
    assert hp._typing_audience_cache.stats()["size"] == 1
    assert hp.invalidate_typing_audience_cache() == 1
    assert hp._typing_audience_cache.stats()["size"] == 0


@pytest.mark.asyncio
async def test_disconnect_invalidates_typing_audience(monkeypatch):
    """A presence transition (disconnect) invalidates warm audiences."""
    import src.api.websocket.gateway as gateway

    invalidations = []
    monkeypatch.setattr(
        hp,
        "invalidate_typing_audience_cache",
        lambda *a, **k: invalidations.append((a, k)) or 1,
    )

    class _Presence:
        def set_status(self, *args, **kwargs):
            return None

        def clear_all_typing(self, user_id):
            return [CHANNEL]

    class _Servers:
        def get_all_shared_member_ids(self, user_id):
            return []

    class _Relationships:
        def get_friend_ids(self, user_id):
            return []

        def get_all_blocked_ids(self, user_id):
            return []

    class _Dispatcher:
        async def dispatch_event(self, *args, **kwargs):
            return 0

    class _Sessions:
        def get_user_connection_count(self, user_id):
            return 0

    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    monkeypatch.setattr(api, "get_relationships", lambda: _Relationships())
    monkeypatch.setattr(api, "get_messaging", lambda: None)

    import src.core.database as db

    monkeypatch.setattr(db, "valkey_available", lambda: False)

    await gateway._dispatch_offline_presence(1, _Presence(), _Dispatcher(), _Sessions())
    assert len(invalidations) == 1


# --- cache primitive: bounded, expiring, copy-safe ----------------------------


def test_cache_is_bounded_lru():
    clock = [0.0]
    cache = TypingAudienceCache(max_entries=3, ttl_seconds=100, clock=lambda: clock[0])
    for cid in range(6):
        cache.put(GUILD, cid, [1, cid])

    assert cache.stats()["size"] == 3
    # Oldest channels evicted; the three most recent remain.
    assert cache.get(GUILD, 3) == [1, 3]
    assert cache.get(GUILD, 5) == [1, 5]
    assert cache.get(GUILD, 0) is None
    assert cache.stats()["max_entries"] == 3


def test_cache_expires_on_epoch_change():
    clock = [0.0]
    cache = TypingAudienceCache(max_entries=8, ttl_seconds=10, clock=lambda: clock[0])
    cache.put(GUILD, CHANNEL, [2, 3])
    assert cache.get(GUILD, CHANNEL) == [2, 3]

    clock[0] = 11.0  # advance past the TTL window
    assert cache.get(GUILD, CHANNEL) is None  # old-epoch key never matches
    cache.put(GUILD, CHANNEL, [4, 5])  # write prunes the stale epoch
    assert cache.get(GUILD, CHANNEL) == [4, 5]
    assert cache.stats()["size"] == 1


def test_cached_viewers_are_returned_by_value():
    cache = TypingAudienceCache(clock=lambda: 0.0)
    cache.put(GUILD, CHANNEL, [2, 3])

    first = cache.get(GUILD, CHANNEL)
    first.append(999)  # caller mutation must not corrupt the cached value
    assert cache.get(GUILD, CHANNEL) == [2, 3]
