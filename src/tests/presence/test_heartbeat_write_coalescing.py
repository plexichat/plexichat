"""Regression tests for bounded coalescing of repeated heartbeat writes.

``update_last_seen`` runs on every gateway heartbeat, and a user commonly has
several connections (plus retries) producing several frames in the same second.
The 30-second DB throttle capped the persisted UPDATE but every frame still
paid for a throttle lookup, a full presence rehydration and a cache write.
These tests pin the new behaviour: one real write per short window, a hard
bound on retained claims, and no loss of correctness on invalidation,
window boundaries, or returned ``last_seen`` values.
"""

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, "/root/code-plexichat/plexichat/src/utils")

from src.core.presence.heartbeat_coalesce import HeartbeatWriteCoalescer
from src.core.presence.manager import PresenceManager
from src.core.presence.manager.composer import _heartbeat_write_coalescer

USER = 556_000_111
START_MS = 1_700_000_000_000


class CountingDB:
    def __init__(self):
        self.reads = 0
        self.writes = 0
        self.last_seen = START_MS - 60_000

    def fetch_one(self, sql, params=()):
        self.reads += 1
        s = sql.lower()
        if "select 1 from" in s:
            return {"1": 1}
        if "select last_seen from pres_presence" in s:
            return {"last_seen": self.last_seen}
        if " from pres_presence p" in s:
            return {
                "status": "online",
                "last_seen": self.last_seen,
                "updated_at": self.last_seen,
                "cs_text": None,
                "cs_emoji": None,
                "cs_expires": None,
                "cs_created": None,
                "activity_type": None,
                "act_name": None,
                "act_details": None,
                "act_url": None,
                "act_state": None,
                "start_timestamp": None,
                "end_timestamp": None,
                "large_image": None,
                "large_text": None,
                "small_image": None,
                "small_text": None,
                "user_id": USER,
            }
        return None

    def fetch_all(self, sql, params=()):
        self.reads += 1
        return []

    def execute(self, sql, params=()):
        self.writes += 1
        if "update pres_presence set last_seen" in sql.lower():
            self.last_seen = params[0]
        return SimpleNamespace(rowcount=1)

    def insert_or_ignore(self, *a, **kw):
        self.writes += 1
        return None


class FakeValkey:
    def __init__(self):
        self.ops = 0

    def sismember(self, key, member):
        self.ops += 1
        return True

    def expire(self, key, ttl):
        self.ops += 1

    def sadd(self, key, val):
        self.ops += 1

    def srem(self, key, val):
        self.ops += 1

    def hgetall(self, key):
        self.ops += 1
        return {}

    def smembers(self, key):
        self.ops += 1
        return set()

    def delete(self, key):
        self.ops += 1


@pytest.fixture(autouse=True)
def clean_coalescer():
    original_clock = _heartbeat_write_coalescer._clock
    _heartbeat_write_coalescer.clear()
    yield
    _heartbeat_write_coalescer.clear()
    _heartbeat_write_coalescer._clock = original_clock


@contextmanager
def _manager():
    db, valkey = CountingDB(), FakeValkey()
    now = [START_MS]
    cache = {}
    cache_writes = [0]

    def fake_ts(_self):
        now[0] += 1
        return now[0]

    def fake_cache_set(key, value, **kw):
        cache_writes[0] += 1
        cache[key] = value
        return True

    def fake_get_cached(user_id):
        return cache.get(f"presence:{user_id}")

    monotonic = [1000.0]
    _heartbeat_write_coalescer._clock = lambda: monotonic[0]
    _heartbeat_write_coalescer.clear()

    patches = [
        patch.object(
            PresenceManager, "_load_config", lambda self: {"timeout_ms": 300000}
        ),
        patch.object(PresenceManager, "_get_timestamp", fake_ts),
        patch.object(PresenceManager, "_validate_user", lambda self, uid: None),
        patch.object(
            PresenceManager, "_revalidate_online_users_set", lambda self: None
        ),
        patch("src.core.presence.manager.presence.valkey_available", return_value=True),
        patch(
            "src.core.presence.manager.presence.get_valkey_client", return_value=valkey
        ),
        patch("src.core.database.cache_set", fake_cache_set),
        patch("src.core.presence.manager.presence.cache_set", fake_cache_set),
        patch(
            "src.core.presence.manager.presence.get_cached_presence",
            fake_get_cached,
        ),
    ]
    for p in patches:
        p.start()
    try:
        yield PresenceManager(db), db, cache_writes, monotonic
    finally:
        for p in reversed(patches):
            p.stop()


def _heartbeat(pm, monotonic, count, spacing=0.05):
    results = []
    for _ in range(count):
        monotonic[0] += spacing
        results.append(pm.update_last_seen(USER))
    return results


# ---------------------------------------------------------------------------
# Coalescing + bounding
# ---------------------------------------------------------------------------


@pytest.mark.presence
def test_heartbeat_burst_collapses_to_one_write_per_window():
    with _manager() as (pm, db, cache_writes, monotonic):
        pm.update_last_seen(USER)  # warm the window
        db.writes = 0
        cache_writes[0] = 0
        results = _heartbeat(pm, monotonic, 100)

    # 100 frames over 5 s (50 ms apart) used to issue 100 cache writes and
    # ~200 DB writes. A 1 s window allows only a handful of real writes.
    assert 1 <= cache_writes[0] <= 7
    assert db.writes <= 2 * cache_writes[0]
    assert _heartbeat_write_coalescer.stats()["coalesced"] >= 90
    # Coalesced calls still hand back the persisted snapshot, never a
    # fabricated timestamp, and it never regresses during the burst.
    assert all(r.last_seen == results[0].last_seen for r in results)
    assert results[0].last_seen > START_MS - 60_000


@pytest.mark.presence
def test_window_boundary_forces_a_fresh_write():
    with _manager() as (pm, db, cache_writes, monotonic):
        monotonic[0] = 1000.0
        pm.update_last_seen(USER)
        monotonic[0] = 1000.9
        pm.update_last_seen(USER)
        writes_in_window = cache_writes[0]
        monotonic[0] = 1001.0  # exact boundary -> new window
        pm.update_last_seen(USER)

    assert writes_in_window == 1
    assert cache_writes[0] == 2


@pytest.mark.presence
def test_tracker_is_bounded_under_distinct_user_flood():
    tracker = HeartbeatWriteCoalescer(window_seconds=60, max_entries=8)
    for uid in range(500):
        tracker.claim(uid)
    assert tracker.stats()["size"] == 8
    assert tracker.stats()["max_entries"] == 8


@pytest.mark.presence
def test_claim_without_completed_write_is_not_reused():
    """A concurrent caller that claimed but has not recorded yet must write."""
    tracker = HeartbeatWriteCoalescer(window_seconds=1.0, clock=lambda: 0.0)
    first = tracker.claim(USER)
    assert first.write is True
    second = tracker.claim(USER)
    assert second.write is True  # no recorded last_seen yet -> no coalescing
    tracker.record(USER, START_MS)
    third = tracker.claim(USER)
    assert third.write is False
    assert third.last_seen == START_MS


# ---------------------------------------------------------------------------
# Correctness guards
# ---------------------------------------------------------------------------


@pytest.mark.presence
def test_invalidation_forces_a_new_write():
    with _manager() as (pm, db, cache_writes, monotonic):
        pm.update_last_seen(USER)
        assert _heartbeat_write_coalescer.invalidate(USER) == 1
        pm.update_last_seen(USER)

    assert cache_writes[0] == 2


@pytest.mark.presence
def test_coalescing_does_not_require_valkey():
    """Presence cache works without Valkey, so coalescing is still valid."""
    with _manager() as (pm, db, cache_writes, monotonic):
        with patch(
            "src.core.presence.manager.presence.valkey_available", return_value=False
        ):
            _heartbeat(pm, monotonic, 25)

    assert cache_writes[0] <= 3
    assert _heartbeat_write_coalescer.stats()["coalesced"] >= 20


@pytest.mark.presence
def test_warm_cache_read_is_served_without_db_write():
    """A coalesced frame reads the cache, so no DB round-trip/write occurs."""
    with _manager() as (pm, db, cache_writes, monotonic):
        pm.update_last_seen(USER)
        db.writes = 0
        db.reads = 0
        monotonic[0] += 0.01
        pm.update_last_seen(USER)

    assert db.writes == 0
    assert cache_writes[0] == 1
