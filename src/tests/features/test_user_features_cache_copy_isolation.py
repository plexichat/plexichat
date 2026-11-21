"""Cached user-feature verdicts must be detached from every caller.

``get_user_features`` memoizes a mutable ``UserFeatures`` dataclass (which also
carries a mutable ``badges`` list) process-wide, and every rate-limit, feature
and badge decision funnels through it.  Before the fix the memoized instance
was returned by reference on both the cold and the warm path, so a single
caller doing ``features.rate_limit_tier = "premium"`` or
``features.badges.append("admin")`` silently widened those gates for every
later request for the whole TTL.

Tests here are deliberately behavioural: they mutate a returned object and
assert a *later decision* stays pristine.  Replacing ``_copy_user_features``
with an identity function makes all of them fail, so they cannot pass
vacuously.
"""

import pytest

from src.core import features


class _FakeDB:
    """Minimal DB double serving one ``user_features`` row."""

    def __init__(self, row):
        self._row = row
        self.reads = 0

    def fetch_one(self, sql, params=()):
        self.reads += 1
        return self._row

    def execute(self, sql, params=()):
        return None

    @staticmethod
    def row(uid=1, tier="standard", badges='["staff"]'):
        return {
            "id": uid,
            "user_id": uid,
            "rate_limit_tier": tier,
            "badges": badges,
            "granted_by": None,
            "granted_at": 0,
            "expires_at": None,
            "notes": None,
        }


@pytest.fixture
def isolated_features_cache(monkeypatch):
    """Give each test a private cache and restore module state afterwards."""
    old_db = features._db
    old_setup = features._setup_complete
    old_ttl = features._FEATURES_CACHE_TTL
    monkeypatch.setattr(features, "_FEATURES_CACHE_TTL", 60.0)
    with features._features_cache_lock:
        features._features_cache.clear()
        features._FEATURES_CACHE_STATS.update(hits=0, misses=0, evictions=0)
    yield
    with features._features_cache_lock:
        features._features_cache.clear()
    features._db = old_db
    features._setup_complete = old_setup
    features._FEATURES_CACHE_TTL = old_ttl


def _memoized_value(uid: int = 1):
    with features._features_cache_lock:
        return next(
            entry[1] for key, entry in features._features_cache.items() if key[1] == uid
        )


def test_cold_read_cannot_poison_the_memo_or_later_decisions(
    isolated_features_cache,
):
    """Mutating the object returned on a cache miss leaves the verdict clean."""
    db = _FakeDB(_FakeDB.row())
    features.setup(db)

    first = features.get_user_features(1)
    first.rate_limit_tier = "premium"
    first.badges.append("admin")
    first.notes = "tampered"

    memo = _memoized_value()
    assert memo.rate_limit_tier == "standard"
    assert memo.badges == ["staff"]
    assert memo.notes is None

    # The decision helpers read the pristine memo, not the mutated caller copy.
    assert db.reads == 1
    assert features.get_user_tier(1) == "standard"
    assert features.get_user_badges(1) == ["staff"]
    assert features.get_user_tier_limits(1).to_dict() == (
        features.get_tier_limits("standard").to_dict()
    )

    later = features.get_user_features(1)
    assert later == memo
    assert later is not first
    assert db.reads == 1  # the later read is still a cache hit


def test_warm_read_cannot_poison_later_decisions(isolated_features_cache):
    """A cache hit hands out a detached copy, not the memoized instance."""
    db = _FakeDB(_FakeDB.row())
    features.setup(db)

    features.get_user_features(1)  # cold
    warm = features.get_user_features(1)  # warm hit -> must be a copy
    assert warm is not _memoized_value()

    warm.rate_limit_tier = "premium"
    warm.badges.append("admin")

    assert features.get_user_tier(1) == "standard"
    assert features.get_user_badges(1) == ["staff"]
    again = features.get_user_features(1)
    assert again.rate_limit_tier == "standard"
    assert again.badges == ["staff"]
    assert again is not warm
    assert db.reads == 1


def test_caller_cannot_grant_a_feature_flag_through_the_cached_object(
    isolated_features_cache,
):
    """An injected flag on a returned copy must not grant it to later checks."""
    db = _FakeDB(_FakeDB.row())
    features.setup(db)

    returned = features.get_user_features(1)
    returned.can_voice = True  # type: ignore[attr-defined]

    assert features.has_feature(1, "can_voice") is False
    assert not hasattr(_memoized_value(), "can_voice")
