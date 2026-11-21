"""Regression tests for O(1) expiry pruning in HeartbeatWriteCoalescer.

``claim()`` runs on every gateway heartbeat frame. The old implementation
pruned expired claims by walking the whole bounded LRU (up to 4096 users) on
every single call, so one frame's cost scaled with the *total* number of tracked
users rather than with the heartbeats that actually needed work. These tests
pin the replacement: identical coalescing semantics, no whole-LRU scan per
claim, and a heap that stays bounded under invalidate/re-claim churn.
"""

import random
import sys
from collections import OrderedDict

sys.path.insert(0, "/root/code-plexichat/plexichat/src/utils")

from src.core.presence.heartbeat_coalesce import HeartbeatWriteCoalescer


class _NaiveCoalescer:
    """Reference implementation: a full-LRU prune on every claim (old cost)."""

    def __init__(self, window, max_entries, clock):
        self.window = window
        self.max_entries = max_entries
        self.clock = clock
        self.entries = OrderedDict()
        self.coalesced = 0
        self.writes = 0

    def claim(self, uid, coalesce=True):
        now = self.clock()
        for u, v in list(self.entries.items()):
            if now - v[0] >= self.window:
                del self.entries[u]
        entry = self.entries.get(uid)
        if (
            coalesce
            and entry is not None
            and entry[1] is not None
            and (now - entry[0]) < self.window
        ):
            self.coalesced += 1
            self.entries.move_to_end(uid)
            return (False, entry[1])
        self.writes += 1
        self.entries[uid] = (now, None)
        self.entries.move_to_end(uid)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return (True, None)

    def record(self, uid, last_seen):
        entry = self.entries.get(uid)
        if entry is not None:
            self.entries[uid] = (entry[0], last_seen)

    def invalidate(self, uid=None):
        if uid is None:
            doomed = list(self.entries)
        else:
            doomed = [uid] if uid in self.entries else []
        for u in doomed:
            self.entries.pop(u, None)
        return len(doomed)


class _CountingOrderedDict(OrderedDict):
    """Track scans of the whole LRU, which a claim must no longer perform."""

    def __init__(self, source):
        super().__init__(source)
        self.item_calls = 0
        self.iter_calls = 0

    def items(self, *a, **kw):
        self.item_calls += 1
        return super().items(*a, **kw)

    def __iter__(self):
        self.iter_calls += 1
        return super().__iter__()


def test_expiry_pruning_matches_full_scan_semantics():
    """Heap pruning is observationally identical to the old full scan."""
    clock = [0.0]
    rng = random.Random(20261121)
    fast = HeartbeatWriteCoalescer(
        window_seconds=2.0, max_entries=8, clock=lambda: clock[0]
    )
    slow = _NaiveCoalescer(2.0, 8, lambda: clock[0])

    for _ in range(4000):
        clock[0] += rng.choice([0.0, 0.01, 0.5, 1.5, 3.0])
        uid = rng.randrange(15)
        op = rng.random()
        if op < 0.75:
            coalesce = rng.random() < 0.9
            got = fast.claim(uid, coalesce=coalesce)
            want = slow.claim(uid, coalesce=coalesce)
            assert (got.write, got.last_seen) == want
        elif op < 0.9:
            seen = rng.randrange(1000, 2000)
            fast.record(uid, seen)
            slow.record(uid, seen)
        else:
            target = None if rng.random() < 0.2 else uid
            assert fast.invalidate(target) == slow.invalidate(target)

        stats = fast.stats()
        assert stats["size"] == len(slow.entries)
        assert stats["coalesced"] == slow.coalesced
        assert stats["writes"] == slow.writes


def test_claim_does_not_scan_the_whole_lru():
    """A warm claim inspects only the heap head, never all 4096 users."""
    clock = [100.0]
    tracker = HeartbeatWriteCoalescer(
        window_seconds=1.0, max_entries=4096, clock=lambda: clock[0]
    )
    for uid in range(4096):
        tracker.claim(uid)
        tracker.record(uid, 1)

    counting = _CountingOrderedDict(tracker._entries)
    tracker._entries = counting

    for _ in range(500):
        claim = tracker.claim(0)
        assert claim.write is False
        assert claim.last_seen == 1

    # The old prune called list(_entries.items()) on every claim (500 scans of
    # 4096 entries). The heap path touches only the claimed id and the heap.
    assert counting.item_calls == 0
    assert counting.iter_calls == 0


def test_expiry_heap_stays_bounded_under_churn():
    """Invalidate/re-claim churn cannot grow the heap past the bounded LRU."""
    clock = [0.0]
    tracker = HeartbeatWriteCoalescer(
        window_seconds=5.0, max_entries=32, clock=lambda: clock[0]
    )
    for step in range(5000):
        clock[0] += 0.001
        uid = step % 200
        tracker.claim(uid)
        tracker.record(uid, step)
        if step % 7 == 0:
            tracker.invalidate(uid)
    assert len(tracker._expiries) <= 2 * 32

    # Everything expired: the next claim prunes the heap down to just itself.
    clock[0] += 100.0
    tracker.claim(1)
    assert len(tracker._expiries) == 1
