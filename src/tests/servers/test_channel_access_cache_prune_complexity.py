"""Regression tests for bounded, constant-cost expiry reclamation.

``ChannelAccessCache.put`` used to call ``_prune_locked``, which scanned every
resident decision (``for key in list(self._entries)``) on *every* write just to
find the few that had expired. With the 2048-entry ceiling that made each write
O(n) -- a cold-decision burst paid ~2k dict lookups per put. Expiry is now
indexed by a min-heap so a write only touches deadlines that actually passed.

These tests pin both halves of the change:

* the per-write work is bounded by the number of *expired* entries, not by the
  number of live ones (``test_put_does_not_scan_live_entries``), and
* the faster reclamation still honours the cache's fail-closed contract:
  real expiry, re-put extension, version bumps and lazy-expiry guards all
  behave exactly as before.
"""

from src.core.servers.channel_access_cache import ChannelAccessCache


class _CountingDict(dict):
    """dict that counts ``get`` calls, to measure work done per write."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_calls = 0

    def get(self, key, default=None):
        self.get_calls += 1
        return super().get(key, default)


class _Clock:
    """Manually advanced monotonic clock for deterministic expiry."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_put_does_not_scan_live_entries():
    """A write into a full cache touches a constant number of expiry slots.

    Before the fix, ``_prune_locked`` issued one ``_expires.get`` per resident
    entry on every put (2048 lookups here). With a heap index and a frozen
    clock, nothing is expired, so no expiry slot is consulted at all.
    """
    clock = _Clock(0.0)
    cache = ChannelAccessCache(max_entries=2048, ttl_seconds=10.0, clock=clock)
    for i in range(2048):
        cache.put(1, 2, 10_000 + i, "channels.view", True)
    assert len(cache) == 2048

    tracked = _CountingDict(cache._expires)
    cache._expires = tracked
    tracked.get_calls = 0

    cache.put(1, 2, 99_999, "channels.view", True)

    # Old code: one get per resident entry (~2048). New code: none, because
    # no live deadline has passed. A small constant leaves room for future
    # non-scanning bookkeeping without ever reintroducing the O(n) sweep.
    assert tracked.get_calls <= 8, f"put scanned {tracked.get_calls} live entries"
    assert cache.get(1, 2, 99_999, "channels.view") is True


def test_expired_entries_are_reclaimed_and_never_served():
    """Past-TTL decisions are reclaimed by a later write and read as misses."""
    clock = _Clock(0.0)
    cache = ChannelAccessCache(max_entries=16, ttl_seconds=10.0, clock=clock)
    cache.put(1, 2, 100, "channels.view", True)
    assert cache.get(1, 2, 100, "channels.view") is True

    clock.now = 11.0
    cache.put(1, 2, 101, "channels.view", True)  # triggers heap reclamation
    assert cache.get(1, 2, 100, "channels.view") is None
    assert len(cache) == 1


def test_lazy_expiry_guard_without_an_intervening_write():
    """A stale entry is a miss on read even if no put has pruned it yet."""
    clock = _Clock(0.0)
    cache = ChannelAccessCache(ttl_seconds=10.0, clock=clock)
    cache.put(1, 2, 200, "messages.read", True)
    clock.now = 25.0  # deadline long past; no put() has run to prune it
    assert cache.get(1, 2, 200, "messages.read") is None


def test_reput_extends_deadline_without_a_stale_tuple_evicting_it():
    """A re-put's fresh decision survives an old heap tuple expiring."""
    clock = _Clock(0.0)
    cache = ChannelAccessCache(max_entries=8, ttl_seconds=10.0, clock=clock)
    cache.put(1, 2, 100, "channels.view", True)
    clock.now = 5.0
    cache.put(1, 2, 100, "channels.view", True)  # fresh deadline 15.0
    clock.now = 10.0
    cache.put(1, 2, 102, "channels.view", True)  # old 10.0 tuple popped
    assert cache.get(1, 2, 100, "channels.view") is True


def test_bump_and_clear_still_retire_decisions():
    """Version invalidation and flush remain intact with the heap index."""
    clock = _Clock(0.0)
    cache = ChannelAccessCache(ttl_seconds=100.0, clock=clock)
    cache.put(1, 2, 300, "channels.view", True)
    assert cache.get(1, 2, 300, "channels.view") is True

    cache.bump(1, 2)
    assert cache.get(1, 2, 300, "channels.view") is None
    cache.put(1, 2, 300, "channels.view", False)
    assert cache.get(1, 2, 300, "channels.view") is False

    cache.clear()
    assert len(cache) == 0
    assert cache._expiry_heap == []  # no stale tuples survive a flush
    assert cache.get(1, 2, 300, "channels.view") is None
