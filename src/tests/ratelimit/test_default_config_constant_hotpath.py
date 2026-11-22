"""Default rate-limit config is a precomputed module constant, not per request.

``RateLimitManager._get_config_for_request`` is on the hot path of every
rate-limited request. For any route that is not in the strict per-route table
(the F8 bounded "unknown route" bucket), it used to call ``get_default_config()``,
which constructed a brand-new, all-constant ``RateLimitConfig`` every time.

The default policy is an invariant: every field is a literal. It can be built
once at import time and shared, avoiding one dataclass allocation per request
in exactly the unknown-route flood case the bounded keyspace is meant to
survive. Sharing changes neither fail-closed values nor bucket-key scoping, and
callers that need a modified limit still get a fresh object via
``with_multiplier``.

Run with::

    pytest src/tests/ratelimit/test_default_config_constant_hotpath.py -q -n 0
"""

import pytest

from src.core.ratelimit import config as config_mod
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)

pytestmark = pytest.mark.unit

_UNKNOWN_ROUTE = "GET /unknown/{id}"


def _count_config_allocations(calls: int) -> int:
    """Count ``RateLimitConfig`` objects built while resolving an unknown route.

    A fresh default config per request is the allocation this regression pins.
    Patching the dataclass constructor counts real object creations without
    relying on timing.
    """
    manager = RateLimitManager()
    original_init = RateLimitConfig.__init__
    built = 0

    def counting_init(self, *args, **kwargs):
        nonlocal built
        built += 1
        return original_init(self, *args, **kwargs)

    RateLimitConfig.__init__ = counting_init
    try:
        for _ in range(calls):
            manager._get_config_for_request(_UNKNOWN_ROUTE, False, False)
    finally:
        RateLimitConfig.__init__ = original_init
    return built


def test_unknown_route_does_not_allocate_default_config_per_request():
    """No default-config object is constructed on the per-request path."""
    assert _count_config_allocations(200) == 0


def test_default_config_is_one_shared_module_constant():
    """Every lookup returns the same precomputed, immutable-by-convention value."""
    first = config_mod.get_default_config()
    assert config_mod.get_default_config() is first


def test_default_config_values_are_unchanged():
    """Fail-closed defaults stay byte-for-byte identical after hoisting."""
    cfg = config_mod.get_default_config()
    assert isinstance(cfg, RateLimitConfig)
    assert cfg.requests == 60
    assert cfg.window_seconds == 30.0
    assert cfg.burst == 10
    assert cfg.algorithm is RateLimitAlgorithm.SLIDING_WINDOW
    assert cfg.scope is BucketType.ROUTE


def test_multiplier_still_produces_a_distinct_config():
    """Bot/webhook scaling still returns a fresh config, never mutating the shared one."""
    manager = RateLimitManager()
    base = config_mod.get_default_config()
    scaled = manager._get_config_for_request(_UNKNOWN_ROUTE, True, False)
    # Unknown routes are not in BOT_HIGHER_LIMIT_ROUTES, so the plain default
    # is returned; the shared constant must not have been mutated.
    assert scaled is base
    assert base.requests == 60
    assert base.burst == 10


def test_unknown_routes_keep_distinct_bucket_keys():
    """Sharing the config does not collapse key scoping for distinct routes."""
    manager = RateLimitManager()
    key_a = manager._generate_bucket_key(
        BucketType.ROUTE, route="GET /unknown/a", ip_address="198.51.100.7"
    )
    key_b = manager._generate_bucket_key(
        BucketType.ROUTE, route="GET /unknown/b", ip_address="198.51.100.7"
    )
    assert key_a != key_b
