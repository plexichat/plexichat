"""User-feature cache overflow must evict one LRU key, not flush every user.

Rate limiting and several route helpers resolve a user's tier through
``get_user_features`` on every request. The cache used to clear *all* entries
once it reached its cap, so a deployment with more active users than the cap
periodically dropped every hot user at once and hammered the DB with duplicate
reads. This module pins the bounded-LRU behaviour, the cache's observability
counters, and the security invariants that must survive the change:

* a fresh/repointed database never observes another database's cached row;
* invalidating one user never drops another user's entry (no cross-user leak);
* expired entries are recomputed, and time-bound grants are never cached, so
  an expiry can never be kept alive by the cache.
"""

import time

import pytest

from src.core import features


class _FakeDB:
    """Minimal DB double that counts ``user_features`` reads."""

    def __init__(self):
        self.reads = 0
        self.rows = {}

    def fetch_one(self, sql, params=()):
        self.reads += 1
        uid = params[0]
        return self.rows.get(uid)

    def execute(self, sql, params=()):
        return None

    @staticmethod
    def row(uid, tier="standard", expires_at=None):
        return {
            "id": uid,
            "user_id": uid,
            "rate_limit_tier": tier,
            "badges": "[]",
            "granted_by": None,
            "granted_at": 0,
            "expires_at": expires_at,
            "notes": None,
        }


@pytest.fixture
def isolated_features_cache(monkeypatch):
    """Give each test a private cache and restore module state afterwards."""
    old_db = features._db
    old_setup = features._setup_complete
    old_max = features._FEATURES_CACHE_MAX
    old_ttl = features._FEATURES_CACHE_TTL

    monkeypatch.setattr(features, "_FEATURES_CACHE_MAX", 8)
    monkeypatch.setattr(features, "_FEATURES_CACHE_TTL", 60.0)
    with features._features_cache_lock:
        features._features_cache.clear()
        features._FEATURES_CACHE_STATS.update(hits=0, misses=0, evictions=0)

    yield

    with features._features_cache_lock:
        features._features_cache.clear()
    features._db = old_db
    features._setup_complete = old_setup
    features._FEATURES_CACHE_MAX = old_max
    features._FEATURES_CACHE_TTL = old_ttl


def _users_in_cache():
    with features._features_cache_lock:
        return {key[1] for key in features._features_cache}


def test_overflow_evicts_only_lru_key(isolated_features_cache):
    """One overflow evicts one key, leaving hot users cached."""
    db = _FakeDB()
    for uid in range(1, 9):
        db.rows[uid] = _FakeDB.row(uid)
    features.setup(db)

    for uid in range(1, 9):
        features.get_user_features(uid)
    assert len(features._features_cache) == 8

    # Make user 1 the most recently used before pushing the store over the cap.
    features.get_user_features(1)
    db.reads = 0

    # The ninth distinct user triggers exactly one LRU eviction.
    db.rows[9] = _FakeDB.row(9)
    features.get_user_features(9)
    assert features.get_features_cache_stats()["evictions"] == 1
    assert len(features._features_cache) == 8
    # User 2 was the least recently used; user 1 (hot) must survive.
    assert 2 not in _users_in_cache()
    assert 1 in _users_in_cache()
    assert 9 in _users_in_cache()

    # A full flush would re-query these users; LRU keeps them cached.
    db.reads = 0
    for _ in range(100):
        features.get_user_features(1)
        features.get_user_features(3)
    assert db.reads == 0


def test_cache_is_db_scoped_and_invalidation_is_per_user(isolated_features_cache):
    """Repointing the DB or invalidating one user cannot leak another's row."""
    db_a = _FakeDB()
    db_a.rows[1] = _FakeDB.row(1, tier="alpha")
    db_a.rows[2] = _FakeDB.row(2, tier="premium")
    features.setup(db_a)

    assert features.get_user_tier(1) == "alpha"
    assert features.get_user_tier(2) == "premium"
    # Two warm reads must agree on value (the cache is doing its job) but must
    # not be the same object: the memoized verdict is never handed out directly.
    first = features.get_user_features(1)
    second = features.get_user_features(1)
    assert first == second
    assert first is not second

    # Dropping user 1 leaves user 2's cached row untouched.
    features._invalidate_user_features_cache(1)
    with features._features_cache_lock:
        assert all(key[1] != 1 for key in features._features_cache)
    assert 2 in _users_in_cache()

    # A different DB object must not observe user 2's cached premium tier.
    db_b = _FakeDB()
    db_b.rows[2] = _FakeDB.row(2, tier="standard")
    features.setup(db_b)
    assert features.get_user_tier(2) == "standard"
    assert features.get_user_features(2).rate_limit_tier == "standard"


def test_expired_entry_is_recomputed(isolated_features_cache, monkeypatch):
    """An expired cache entry is never served; the row is read again."""
    db = _FakeDB()
    db.rows[7] = _FakeDB.row(7, tier="alpha")
    features.setup(db)

    assert features.get_user_tier(7) == "alpha"
    assert db.reads == 1
    db.rows[7] = _FakeDB.row(7, tier="standard")

    with features._features_cache_lock:
        expiry = next(
            entry[0] for key, entry in features._features_cache.items() if key[1] == 7
        )

    class _Clock:
        now = expiry + 1.0

        def monotonic(self):
            return self.now

        def time(self):
            return self.now / 1000.0

    # Move only the features module's clock forward past the entry's TTL.
    monkeypatch.setattr(features, "time", _Clock())
    assert features.get_user_tier(7) == "standard"
    assert db.reads == 2


def test_time_bound_grant_is_never_cached(isolated_features_cache):
    """A grant with an expiry bypasses the cache so expiry stays authoritative."""
    db = _FakeDB()
    db.rows[5] = _FakeDB.row(
        5, tier="alpha", expires_at=int(time.time() * 1000) + 60_000
    )
    features.setup(db)

    features.get_user_features(5)
    features.get_user_features(5)
    assert db.reads == 2
    assert 5 not in _users_in_cache()
