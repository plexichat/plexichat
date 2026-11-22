"""Regression tests for O(expired) expiry pruning in TypingWriteCoalescer.

``claim()`` runs on every TYPING_START frame. The old implementation pruned
expired claims by walking the whole bounded LRU (up to 4096 (user, channel)
pairs) on every single call, so one frame's cost scaled with the *total* number
of tracked pairs rather than with the claims that actually needed pruning.

These tests pin the replacement: identical coalescing semantics (including
``invalidate``/``record``/fail-closed ``coalesce=False``), no whole-LRU scan per
claim, and a deadline heap that stays bounded under churn.
"""

import random
import sys
from collections import OrderedDict

sys.path.insert(0, "/root/code-plexichat/plexichat/src/utils")

from src.core.presence.typing_coalesce import TypingWriteCoalescer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NaiveCoalescer:
    """Reference implementation: a full-LRU prune on every claim (old cost)."""

    def __init__(self, window, max_entries, clock):
        self.window = window
        self.max_entries = max_entries
        self.clock = clock
        self.entries = OrderedDict()
        self.coalesced = 0
        self.writes = 0

    @staticmethod
    def _key(key):
        return (int(key[0]), int(key[1]))

    def claim(self, key, coalesce=True):
        key = self._key(key)
        now = self.clock()
        for k, v in list(self.entries.items()):
            if now - v[0] >= self.window:
                del self.entries[k]
        entry = self.entries.get(key)
        if coalesce and entry is not None and (now - entry[0]) < self.window:
            self.coalesced += 1
            self.entries.move_to_end(key)
            return (False, entry[1], entry[2])
        self.writes += 1
        started = entry[1] if entry is not None else 0
        expires = entry[2] if entry is not None else 0
        self.entries[key] = (now, started, expires)
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return (True, None, None)

    def record(self, key, started_at, expires_at):
        key = self._key(key)
        entry = self.entries.get(key)
        if entry is not None:
            self.entries[key] = (entry[0], int(started_at), int(expires_at))

    def invalidate(self, key=None, user_id=None):
        if key is None and user_id is None:
            doomed = list(self.entries)
        else:
            key = self._key(key) if key is not None else None
            uid = int(user_id) if user_id is not None else None
            doomed = [
                k
                for k in self.entries
                if (key is not None and k == key) or (uid is not None and k[0] == uid)
            ]
        for k in doomed:
            self.entries.pop(k, None)
        return len(doomed)


class _CountingOrderedDict(OrderedDict):
    """Count entries touched by whole-LRU scans (the old per-claim cost)."""

    def __init__(self, source):
        super().__init__(source)
        self.item_calls = 0
        self.scanned_items = 0

    def items(self, *a, **kw):
        view = super().items(*a, **kw)
        self.item_calls += 1
        self.scanned_items += len(view)
        return view

    def __iter__(self):
        # ``invalidate`` also scans; count that separately from claim pruning.
        return super().__iter__()


# ---------------------------------------------------------------------------
# Semantics: heap pruning must be observationally identical to the full scan
# ---------------------------------------------------------------------------


def test_expiry_pruning_matches_full_scan_semantics():
    clock = [0.0]
    rng = random.Random(20261122)
    fast = TypingWriteCoalescer(
        window_seconds=2.0, max_entries=8, clock=lambda: clock[0]
    )
    slow = _NaiveCoalescer(2.0, 8, lambda: clock[0])

    for _ in range(4000):
        clock[0] += rng.choice([0.0, 0.01, 0.5, 1.5, 3.0])
        key = (rng.randrange(8), rng.randrange(3))
        op = rng.random()
        if op < 0.7:
            coalesce = rng.random() < 0.9
            got = fast.claim(key, coalesce=coalesce)
            want = slow.claim(key, coalesce=coalesce)
            assert (got.write, got.started_at, got.expires_at) == want
        elif op < 0.85:
            started = rng.randrange(1_000_000, 2_000_000)
            expires = started + 6000
            fast.record(key, started, expires)
            slow.record(key, started, expires)
        else:
            mode = rng.random()
            if mode < 0.4:
                args = {"key": key}
            elif mode < 0.8:
                args = {"user_id": key[0]}
            else:
                args = {}
            assert fast.invalidate(**args) == slow.invalidate(**args)

        stats = fast.stats()
        assert stats["size"] == len(slow.entries)
        assert stats["coalesced"] == slow.coalesced
        assert stats["writes"] == slow.writes
        assert {k: v[:3] for k, v in fast._entries.items()} == slow.entries


def test_claim_does_not_scan_the_whole_lru():
    """A warm claim inspects only the heap head, never all 4096 pairs."""
    clock = [100.0]
    tracker = TypingWriteCoalescer(
        window_seconds=1.0, max_entries=4096, clock=lambda: clock[0]
    )
    for uid in range(4096):
        tracker.claim((uid, 1))
        tracker.record((uid, 1), 1, 2)

    counting = _CountingOrderedDict(tracker._entries)
    tracker._entries = counting

    for _ in range(500):
        claim = tracker.claim((0, 1))
        assert claim.write is False
        assert claim.started_at == 1
        assert claim.expires_at == 2

    # The old prune walked all 4096 entries on each claim: 500 * 4096 = 2,048,000
    # Python-level visits for zero expiries. With a deadline heap none of that
    # work is needed while the pair is unexpired.
    assert counting.item_calls == 0
    assert counting.scanned_items == 0


def test_expiry_heap_stays_bounded_under_churn():
    """Invalidate/re-claim churn cannot grow the heap past the bounded LRU."""
    clock = [0.0]
    tracker = TypingWriteCoalescer(
        window_seconds=5.0, max_entries=32, clock=lambda: clock[0]
    )
    for step in range(5000):
        clock[0] += 0.001
        key = (step % 200, 1)
        tracker.claim(key)
        tracker.record(key, 1, 2)
        if step % 7 == 0:
            tracker.invalidate(key=key)
    assert len(tracker._expiries) <= 2 * 32

    # Everything expired: the next claim prunes the heap down to just itself.
    clock[0] += 100.0
    tracker.claim((1, 1))
    assert len(tracker._expiries) == 1
