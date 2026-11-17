"""Security regressions for the shared cache helpers.

Three classes of weakness are pinned here:

* ``generate_cache_key`` must not let a value forge additional ``:``-delimited
  key segments (collision / cross-user cache read or poison) nor mint an
  unbounded key from a single oversized argument (memory DoS).
* The invalidation helpers must not splice glob metacharacters into their
  patterns, which would widen an eviction to other users'/servers' entries.
* The in-memory rate-limit map must stay bounded even when every counter is
  fresh, so a flood of distinct keys cannot exhaust process memory.
"""

from typing import cast

import pytest

from src.core.database.cache import operations, rate_limit, session
from src.core.database.cache.base import _mem_cache, mem_cache_get, mem_cache_set
from src.core.database.cache.serialization import generate_cache_key


# ---------------------------------------------------------------------------
# cache key injection / collision / unbounded length
# ---------------------------------------------------------------------------


def test_value_cannot_forge_extra_key_segments():
    # Pre-fix: generate_cache_key("p", "x:str:y") == "p:str:x:str:y", the exact
    # key of the two-argument call below -> a caller could read/poison another
    # caller's entry by choosing a value containing the delimiter.
    assert generate_cache_key("p", "x:str:y") != generate_cache_key("p", "x", "y")


def test_kwarg_name_and_value_cannot_forge_segments():
    # Pre-fix both calls produced "p:x:str:y:int:1".
    a = generate_cache_key("p", **{"x:str:y": 1})
    b = generate_cache_key("p", x="y:int:1")
    assert a != b


def test_numeric_ids_keep_legacy_invalidation_shape():
    # Invalidation globs elsewhere in the app anchor on int:<id> /
    # TokenInfo:<id>; those safe, delimiter-free components must not change.
    token = type("TokenInfo", (), {"user_id": 42})()
    assert generate_cache_key("user_servers", 7, limit=100) == (
        "user_servers:int:7:limit:int:100"
    )
    assert generate_cache_key("user_servers_api", current_user=token) == (
        "user_servers_api:current_user:TokenInfo:42"
    )


def test_oversized_value_is_bounded_and_distinct():
    huge_a = "a" * 5000
    huge_b = "a" * 4999 + "b"
    key_a = generate_cache_key("p", huge_a)
    key_b = generate_cache_key("p", huge_b)
    assert len(key_a) < 200
    assert len(key_b) < 200
    assert key_a != key_b


def test_falsy_user_id_does_not_collide_with_unknown():
    user_zero = type("User", (), {"user_id": 0, "id": 0})()
    user_missing = type("User", (), {})()
    assert generate_cache_key("k", user_zero) != generate_cache_key("k", user_missing)


# ---------------------------------------------------------------------------
# glob injection in invalidation patterns
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline_mem_only(monkeypatch):
    _mem_cache.clear()
    for module in (operations, session, rate_limit):
        monkeypatch.setattr(module, "get_client", lambda: None)
        monkeypatch.setattr(module, "is_available", lambda: False)
    rate_limit._mem_rate_limits.clear()
    yield
    _mem_cache.clear()
    rate_limit._mem_rate_limits.clear()


def test_invalidate_user_servers_rejects_glob_metacharacters():
    victim = "user_servers:int:42:limit:int:100"
    other = "user_servers:int:99:limit:int:100"
    mem_cache_set(victim, ["v"], 60)
    mem_cache_set(other, ["v"], 60)

    # Pre-fix this built "user_servers:int:42:*:*", evicting every user.
    assert session.invalidate_user_servers(cast(int, "42:*")) == 0
    assert mem_cache_get(victim) is not None
    assert mem_cache_get(other) is not None


def test_invalidate_server_channels_rejects_glob_metacharacters():
    victim = "channels:foo:1:bar"
    other = "channels:foo:2:bar"
    mem_cache_set(victim, ["v"], 60)
    mem_cache_set(other, ["v"], 60)

    assert session.invalidate_server_channels(cast(int, "1,*")) == 0
    assert mem_cache_get(victim) is not None
    assert mem_cache_get(other) is not None


def test_invalidate_user_servers_valid_int_still_scoped():
    victim = generate_cache_key("user_servers", 42, limit=100)
    other = generate_cache_key("user_servers", 99, limit=100)
    mem_cache_set(victim, ["v"], 60)
    mem_cache_set(other, ["v"], 60)

    assert session.invalidate_user_servers(42) > 0
    assert mem_cache_get(victim) is None
    assert mem_cache_get(other) is not None


# ---------------------------------------------------------------------------
# bounded in-memory rate-limit map
# ---------------------------------------------------------------------------


def test_mem_rate_limit_map_is_bounded_when_all_counters_are_fresh():
    cap = rate_limit._MEM_RATE_LIMIT_MAX_KEYS
    for i in range(cap + 250):
        rate_limit.check_rate_limit(f"fresh-key-{i}", limit=5, window_seconds=60)

    assert len(rate_limit._mem_rate_limits) <= cap


def test_mem_rate_limit_evicts_least_recently_used():
    cap = rate_limit._MEM_RATE_LIMIT_MAX_KEYS
    for i in range(cap):
        rate_limit.check_rate_limit(f"key-{i}", limit=5, window_seconds=60)

    # Re-touch the oldest key so it is protected, then add a brand new one.
    rate_limit.check_rate_limit("key-0", limit=5, window_seconds=60)
    rate_limit.check_rate_limit("key-new", limit=5, window_seconds=60)

    assert len(rate_limit._mem_rate_limits) == cap
    assert "ratelimit:key-0" in rate_limit._mem_rate_limits
    assert "ratelimit:key-1" not in rate_limit._mem_rate_limits
    assert "ratelimit:key-new" in rate_limit._mem_rate_limits
