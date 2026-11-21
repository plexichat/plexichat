"""Per-request tier/feature resolution is version-keyed and short-TTL cached.

``get_rate_limit_multiplier``/``get_user_tier_limits`` resolve a tier on every
rate-limited request. That used to re-read the ``user_features`` config section
and re-run ``validate_tier_limits`` plus a full ``TierLimits`` reconstruction on
each call. The resolved table is now a version-keyed snapshot with a short
staleness ceiling, so the heavy work runs once per config version instead of
once per request.

These tests pin: byte-for-byte identical verdicts with a single compute,
fail-closed behaviour for unknown/invalid tiers, explicit and TTL/version-based
invalidation, and -- critically -- that a revoked admin grant is never served
from either cache.
"""

import copy
import time

import pytest

import utils.config as config
from src.core import features
from src.core.security.hardening import HardeningViolation


@pytest.fixture(autouse=True)
def _fresh_tier_caches():
    """Every test starts and ends with empty tier snapshots."""
    features.invalidate_tier_configuration_cache()
    features.invalidate_tier_limits_cache()
    yield
    features.invalidate_tier_configuration_cache()
    features.invalidate_tier_limits_cache()


def test_repeated_resolution_identical_and_single_compute(monkeypatch):
    """One table build serves every hot-path lookup, with stable verdicts."""
    snapshot = features._tier_limits_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    expected = features._compute_tier_limits("standard")
    results = [features.get_tier_limits("standard") for _ in range(200)]

    # One table build serves every hot-path lookup. The verdicts are equal in
    # value, but each caller gets its own detached instance: a caller mutating
    # its TierLimits must not poison the memoized verdict used by the others.
    assert all(r == expected for r in results)
    assert all(r is not results[0] for r in results[1:])
    assert len({id(r) for r in results}) == len(results)
    assert len(builds) == 1
    assert snapshot.stats()["rebuilds"] == 1
    assert 0 < features._TIER_LIMITS_CACHE_TTL_SECONDS <= 10


def test_cached_verdict_equal_to_authoritative_uncached():
    """Caching changes only how often a verdict is computed, not what it is."""
    cached = features.get_tier_limits("premium")
    fresh = features._compute_tier_limits("premium")
    assert cached == fresh
    assert cached.to_dict() == fresh.to_dict()


def test_unknown_tier_fails_closed_and_is_never_cached(monkeypatch):
    """An unknown tier keeps raising on every call; no grant is inferred."""
    real = features._compute_tier_limits
    calls = []

    def counting(tier):
        calls.append(tier)
        return real(tier)

    monkeypatch.setattr(features, "_compute_tier_limits", counting)

    for _ in range(5):
        with pytest.raises(features.InvalidTierError):
            features.get_tier_limits("definitely_not_a_tier")

    assert calls.count("definitely_not_a_tier") == 5


def test_invalid_config_fails_closed_and_is_not_cached(monkeypatch):
    """A validation error is never memoized; fixing config is seen at once."""
    loader = config._config_instance
    mutated = copy.deepcopy(loader.config.get("user_features", {}) or {})
    mutated["rate_limit_tiers"] = {"standard": {"multiplier": 9999}}
    monkeypatch.setitem(loader.config, "user_features", mutated)
    loader._invalidate_cache()
    features.invalidate_tier_limits_cache()

    with pytest.raises(HardeningViolation):
        features.get_tier_limits("standard")

    # The failed build left no cached verdict behind: once the operator config
    # is corrected the very next read is clean.
    mutated["rate_limit_tiers"]["standard"]["multiplier"] = 2.0
    loader._invalidate_cache()
    features.invalidate_tier_limits_cache()
    assert features.get_tier_limits("standard").multiplier == 2.0


def test_version_change_after_ceiling_rebuilds(monkeypatch):
    """The short TTL bounds staleness: a changed version forces one rebuild."""
    snapshot = features._tier_limits_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    before = features.get_tier_limits("standard")
    assert len(builds) == 1

    version = ["v1"]
    monkeypatch.setattr(snapshot, "_version_fn", lambda: tuple(version))
    # Simulate the staleness ceiling elapsing (in production this is TTL secs).
    snapshot._version_checked_at = None
    version[0] = "v2"
    after = features.get_tier_limits("standard")

    assert after == before
    assert len(builds) == 2
    assert snapshot.version == ("v2",)


def test_explicit_invalidation_hook_clears_both_snapshots():
    """The admin hot-swap/config-reload hook drops every cached tier verdict."""
    features.get_tier_limits("standard")
    features.get_tier_configuration()
    assert features._tier_limits_snapshot.version is not None
    assert features._tier_configuration_snapshot.version is not None

    features.invalidate_tier_configuration_cache()

    assert features._tier_limits_snapshot.version is None
    assert features._tier_configuration_snapshot.version is None


def test_config_change_via_invalidation_hook_has_no_stale_limits(monkeypatch):
    """A hot-swapped config value is visible immediately, not for the TTL."""
    loader = config._config_instance
    mutated = copy.deepcopy(loader.config.get("user_features", {}) or {})
    mutated.setdefault("rate_limit_tiers", {})
    standard = copy.deepcopy(mutated["rate_limit_tiers"].get("standard", {}) or {})
    standard["multiplier"] = 4.5
    mutated["rate_limit_tiers"]["standard"] = standard

    before = features.get_tier_limits("standard")
    monkeypatch.setitem(loader.config, "user_features", mutated)
    loader._invalidate_cache()
    features.invalidate_tier_configuration_cache()  # admin hot-swap hook
    after = features.get_tier_limits("standard")

    assert after.multiplier == 4.5
    assert before.multiplier != 4.5


def test_no_stale_grant_after_tier_revoke(db, test_user):
    """A revoked premium grant is never served from a warm cache."""
    features.setup(db)
    features.set_user_features(test_user.id, admin_id=1, rate_limit_tier="premium")
    assert features.get_user_tier(test_user.id) == "premium"
    assert features.get_rate_limit_multiplier(test_user.id) == 3.0

    # Both the per-user row cache and the tier-limits snapshot are warm now.
    # An admin revoke must take effect on the very next read.
    features.set_user_features(test_user.id, admin_id=2, rate_limit_tier="standard")
    assert features.get_user_tier(test_user.id) == "standard"
    assert features.get_rate_limit_multiplier(test_user.id) == 1.0


def test_expired_grant_never_served_from_cache(db, test_user):
    """A time-bound grant that has elapsed cannot grant a tier or a flag."""
    features.setup(db)
    past = int(time.time() * 1000) - 1
    features.set_user_features(
        test_user.id, admin_id=1, rate_limit_tier="alpha", expires_at=past
    )
    # The row exists but the grant has expired, so neither the per-user cache
    # nor the cached tier table may keep it alive.
    assert features.get_user_features(test_user.id) is not None
    assert features.get_user_tier(test_user.id) == features.get_default_tier()
    assert features.has_feature(test_user.id, "can_voice") is False


def test_mutated_tier_verdict_cannot_poison_later_decisions(monkeypatch):
    """An in-place edit of a returned verdict must not widen any later gate."""
    first = features.get_tier_limits("standard")
    baseline = first.to_dict()

    # A caller (or a bug holding onto the returned object) tries to widen the
    # upload/rate-limit/emoji-slot gates through the object it was handed.
    first.multiplier = 1000.0
    first.max_file_size_mb = -1
    first.custom_emoji_slots = -1

    # The warm read is detached from the memo and pristine.
    second = features.get_tier_limits("standard")
    assert second is not first
    assert second.to_dict() == baseline
    assert features._tier_limits_snapshot.snapshot()["standard"].to_dict() == baseline

    # The decision that consumes the verdict is unaffected.
    monkeypatch.setattr(features, "get_user_tier", lambda _uid: "standard")
    assert features.get_rate_limit_multiplier(1) == baseline["multiplier"]


def test_mutated_tier_configuration_cannot_poison_cached_table():
    """The admin tier table copy must not expose the cached mutable objects."""
    first = features.get_tier_configuration()
    baseline = {tier: limits.to_dict() for tier, limits in first.limits.items()}

    first.limits["standard"].multiplier = 999.0
    first.tiers.append("rogue")
    first.default = "rogue"

    second = features.get_tier_configuration()
    assert second is not first
    assert second.tiers == features._tier_configuration_snapshot.snapshot().tiers
    assert "rogue" not in second.tiers
    assert second.default == features._tier_configuration_snapshot.snapshot().default
    assert {tier: limits.to_dict() for tier, limits in second.limits.items()} == baseline
