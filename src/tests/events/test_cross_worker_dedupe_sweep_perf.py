"""PERF regression: cross-worker dedupe expiry sweep is O(expired), not O(N).

``CrossWorkerDedupe.is_duplicate`` runs on every ingested cross-worker event
(the shared gateway hot path).  Its registry is hard-bounded at ``max_entries``
(4096 in production), and the old sweep rebuilt a list by scanning *every*
entry on every call — a per-event O(max_entries) cost plus a throwaway list
allocation, even when nothing had expired.

The registry is kept ordered oldest-first by ``_store``, so expired ids are
always a prefix.  The sweep now walks only that prefix and stops at the first
live entry: the common case is one O(1) check.  These tests pin both that
algorithmic property and the ordering invariant that makes it correct (a
publisher retry re-stores an id and must refresh its position, not leave a
stale timestamp at the front that would hide genuinely expired entries behind
it).

Pure unit test: no app import, config, DB or network.  Uses a deterministic
fake monotonic clock so timing never depends on wall-clock sleeps.
"""

from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace

import pytest

from src.core.events import dedupe
from src.core.events.dedupe import CrossWorkerDedupe

pytestmark = pytest.mark.unit


class _CountingOrderedDict(OrderedDict):
    """Registry stand-in that counts how many entries iteration yields.

    Both ``__iter__`` and ``items`` are instrumented so the detector catches
    either sweep implementation: the old full scan iterated every entry via
    ``items()``, the new prefix sweep takes one entry via ``iter()``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations = 0

    def __iter__(self):
        for key in super().__iter__():
            self.iterations += 1
            yield key

    def items(self):
        for item in super().items():
            self.iterations += 1
            yield item


def _install_counting_registry(cache):
    counted = _CountingOrderedDict(cache._seen)
    cache._seen = counted
    return counted


def test_sweep_touches_only_expired_prefix_not_whole_registry():
    """A live 256-id registry must not be rescanned on every ingestion.

    Before: each ``is_duplicate`` scanned all 256 entries -> 25,600 visits for
    100 calls.  After: the first entry is live, so each call touches exactly
    one entry -> ~100 visits.  The generous ceiling distinguishes the two
    algorithms without being sensitive to small constant-factor changes.
    """
    cache = CrossWorkerDedupe(max_entries=256, ttl_sec=300.0)
    for i in range(256):
        cache.is_duplicate(f"evt-{i}")

    counted = _install_counting_registry(cache)
    counted.iterations = 0
    for n in range(100):
        # All of these are duplicates and none is expired.
        assert cache.is_duplicate(f"evt-{n}") is True

    assert counted.iterations < 100 * 4, (
        "expiry sweep regressed to an O(N) full scan: "
        f"{counted.iterations} entry visits for 100 hot-path calls"
    )


def test_refreshed_retry_does_not_hide_expired_entries_behind_it(monkeypatch):
    """A re-stored id moves to the back; the prefix sweep stays correct.

    The publisher can retry a burst with the same envelope id, which refreshes
    that id's timestamp.  If the refresh left a *newer* timestamp parked at the
    front, a prefix sweep would stop early and never evict older entries
    behind it.  Re-storing must move the id to the end so time order holds.
    """
    clock = {"now": 1000.0}
    monkeypatch.setattr(dedupe, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    cache = CrossWorkerDedupe(max_entries=16, ttl_sec=10.0)

    clock["now"] = 1000.0
    cache.remember("older")  # ts=1000
    clock["now"] = 1005.0
    cache.remember("mid")  # ts=1005
    clock["now"] = 1008.0
    cache.remember("older")  # retry: must move to back with ts=1008

    # cutoff=1006: "mid" (1005) is expired, refreshed "older" (1008) is live.
    clock["now"] = 1016.0
    assert cache.is_duplicate("mid") is False  # expired -> reprocessed
    assert cache.is_duplicate("older") is True  # still within its TTL


def test_full_sweep_still_evicts_all_expired_entries(monkeypatch):
    """When many entries expire at once, all of them are dropped exactly once."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(dedupe, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    cache = CrossWorkerDedupe(max_entries=16, ttl_sec=5.0)

    for i in range(8):
        cache.remember(f"evt-{i}")

    clock["now"] = 1010.0  # every entry is past its 5s TTL
    assert cache.is_duplicate("evt-0") is False
    assert cache.metrics()["size"] == 1, (
        "all 8 expired ids should have been swept; only the just-reprocessed id remains"
    )
    assert cache.metrics()["evictions"] == 8
