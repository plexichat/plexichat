"""Guard that cached policy/identity lookups are actually served from cache.

A cache that is present but always misses (bad key, premature invalidation, a
dependency that bypasses the decorator) is worse than no cache: every request
still pays the query and the extra hash.  These tests clear the in-memory tier
and the global cache counters, then call each hot lookup twice and require the
*call counts* to show a first-call miss followed by a hit.

Run with: pytest -m performance -n 0 src/tests/unit/test_perf_guard_cache_hits.py
"""

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.database.cache.decorators import (
    get_cache_stats,
    reset_cache_stats_internal,
)

pytestmark = pytest.mark.performance


def _assert_miss_then_hit(call, label):
    """Require one cold miss and at least one warm hit on the second call."""
    mem_cache_clear_pattern("*")
    reset_cache_stats_internal()

    call()
    after_first = dict(get_cache_stats())

    call()
    after_second = dict(get_cache_stats())

    first_misses = after_first["misses"]
    first_hits = after_first["hits"]
    second_hits = after_second["hits"] - first_hits

    assert first_misses >= 1, (
        f"{label} did not consult the cache on the cold call "
        f"(misses={first_misses}, hits={first_hits})"
    )
    assert second_hits >= 1, (
        f"{label} was not served from cache on the repeat call "
        f"(hits delta={second_hits}, stats={after_second})"
    )


def test_cached_token_lookup_hits(auth_manager, test_user_with_token):
    """Repeated token verification on one token must hit the cache."""
    token = test_user_with_token["token"]
    _assert_miss_then_hit(lambda: auth_manager.verify_token(token), "verify_token")


def test_cached_user_lookup_hits(auth_manager, test_user_with_token):
    """Repeated current-user loads must hit the cache."""
    user_id = test_user_with_token["user"].id
    _assert_miss_then_hit(lambda: auth_manager.get_user(user_id), "get_user")


def test_cached_permission_policy_lookup_hits(server_manager, test_server):
    """Repeated permission checks are policy lookups on every hot route."""
    server, user = test_server
    _assert_miss_then_hit(
        lambda: server_manager.has_permission(user.id, server.id, "VIEW_CHANNEL"),
        "has_permission",
    )
