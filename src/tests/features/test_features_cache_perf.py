"""User-feature tier lookups are cached with write-through invalidation.

``get_user_tier``/``has_feature``/``get_rate_limit_multiplier`` all resolve a
user's features through ``get_user_features``. That DB read used to run on every
call even for an unchanged user. Verify the short-TTL cache collapses repeated
reads and that every mutation path invalidates it (so permissions never go
stale), while time-bound grants stay uncached so expiry remains authoritative.
"""

import time
from unittest.mock import patch

from src.core import features


def _count_user_feature_reads(db):
    """Wrap ``db.fetch_one`` and count user_features SELECTs."""
    real_fetch_one = db.fetch_one
    calls = []

    def counting(sql, params=()):
        if "FROM user_features" in sql:
            calls.append((sql, tuple(params)))
        return real_fetch_one(sql, params)

    patcher = patch.object(db, "fetch_one", side_effect=counting)
    return patcher, calls


class TestUserFeaturesCache:
    def test_repeated_reads_hit_cache(self, db, test_user):
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="alpha")
        # Re-setup clears the cache but keeps the persisted row.
        features.setup(db)

        patcher, calls = _count_user_feature_reads(db)
        with patcher:
            first = features.get_user_features(test_user.id)
            second = features.get_user_features(test_user.id)

        # A hit is a value-equal but distinct object: callers can never reach
        # the memoized instance and mutate shared tier/flag/badge state.
        assert first == second
        assert first is not second
        assert len(calls) == 1

    def test_write_invalidates_cached_tier(self, db, test_user):
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="premium")
        assert features.get_user_tier(test_user.id) == "premium"

        # A write through the API must invalidate the cached row so the next
        # read observes the new tier.
        features.set_user_features(test_user.id, admin_id=2, rate_limit_tier="alpha")
        assert features.get_user_tier(test_user.id) == "alpha"

    def test_badge_write_invalidates_cached_badges(self, db, test_user):
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1)
        assert features.get_user_badges(test_user.id) == []

        features.add_badge(test_user.id, admin_id=1, badge="staff")
        assert features.get_user_badges(test_user.id) == ["staff"]

    def test_setup_clears_cache(self, db, test_user):
        features.setup(db)
        features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="alpha")
        assert features.get_user_tier(test_user.id) == "alpha"

        # setup() clears the cache; with a fresh DB identity the same user has
        # no features row and falls back to the default tier.
        class _EmptyDB:
            def fetch_one(self, sql, params=()):
                return None

            def execute(self, sql, params=()):
                return None

        try:
            features.setup(_EmptyDB())
            assert features.get_user_tier(test_user.id) == "standard"
        finally:
            features.setup(db)

    def test_expiring_grant_is_not_cached(self, db, test_user):
        features.setup(db)
        future = int(time.time() * 1000) + 3_600_000
        features.set_user_features(
            test_user.id, admin_id=1, rate_limit_tier="alpha", expires_at=future
        )
        features.setup(db)

        patcher, calls = _count_user_feature_reads(db)
        with patcher:
            features.get_user_features(test_user.id)
            features.get_user_features(test_user.id)

        assert len(calls) == 2
