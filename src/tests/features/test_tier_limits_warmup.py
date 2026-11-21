"""Startup warm-up primes the tier-limit snapshots off the request path.

The rate-limiting middleware and the tier helper resolve a tier on every
request. Without warm-up, the first request after a restart pays the config
read + ``validate_tier_limits`` + full ``TierLimits`` reconstruction. The
background warm-up worker builds both single-entry, version-keyed snapshots
once, so that cold cost never lands on a user request.

These tests pin: the first read is served from a warm cache, warming is
idempotent within a config version, failures never break startup, and the
global warm-up loop actually invokes the hook.
"""

import pytest

from src.api import warmup
from src.core import features


@pytest.fixture(autouse=True)
def _fresh_caches():
    features.invalidate_tier_configuration_cache()
    features.invalidate_tier_limits_cache()
    yield
    features.invalidate_tier_configuration_cache()
    features.invalidate_tier_limits_cache()


def test_warmup_primes_both_snapshots():
    """A single warm call builds both tables; a second call is a no-op."""
    limits = features._tier_limits_snapshot
    config = features._tier_configuration_snapshot
    assert limits.version is None and config.version is None
    limits_before = limits.stats()["rebuilds"]
    config_before = config.stats()["rebuilds"]

    elapsed = features.warm_tier_limits_cache()

    assert elapsed >= 0
    assert limits.version is not None
    assert config.version is not None
    # The admin builder also primes the per-request limits table, so one warm
    # call builds each snapshot exactly once.
    assert limits.stats()["rebuilds"] - limits_before == 1
    assert config.stats()["rebuilds"] - config_before == 1

    features.warm_tier_limits_cache()
    assert limits.stats()["rebuilds"] - limits_before == 1
    assert config.stats()["rebuilds"] - config_before == 1


def test_first_request_after_warmup_is_served_from_cache(monkeypatch):
    """A warmed table means the first lookup runs no loader."""
    snapshot = features._tier_limits_snapshot
    builds = []
    real_loader = snapshot._loader

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    features.warm_tier_limits_cache()
    assert len(builds) >= 1, "warming must build the resolved table"
    builds_after_warm = len(builds)

    verdict = features.get_tier_limits("standard")
    assert verdict.multiplier == features._compute_tier_limits("standard").multiplier
    features.get_tier_limits("alpha")
    assert len(builds) == builds_after_warm, (
        "the first request must not rebuild the table"
    )


def test_warmup_is_non_fatal_on_broken_builders(monkeypatch):
    """A bad config must be logged and skipped, never raised into startup."""

    def boom():
        raise RuntimeError("broken builder")

    monkeypatch.setattr(features, "_build_tier_limits_table", boom)
    monkeypatch.setattr(features, "_build_tier_configuration", boom)

    features.warm_tier_limits_cache()  # must not raise


def test_warm_once_invokes_tier_warmup(monkeypatch):
    """The global startup warm-up loop wires in the tier hook."""
    called = []
    monkeypatch.setattr(warmup, "_warm_tier_limits", lambda: called.append(True))

    assert warmup.warm_once() >= 0.0
    assert called == [True]
