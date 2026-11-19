"""Regression tests for bounded memoization of route canonicalization.

The manager re-canonicalizes a route the ASGI middleware has already
normalized. Before memoization that meant a full route-table scan and an
acquisition of the process-wide unknown-route lock on *every* request -- even
though canonical routes form a tiny, fixed set. The memo must:

* return exactly the same keys as an uncached normalization (correctness);
* stay bounded when an attacker supplies unbounded raw paths;
* be inert across independent workers (identical keys, shared quota);
* never weaken the fail-closed denial on a broken store.
"""

import threading

import pytest

import src.core.ratelimit.manager.route_keys as rk
import src.core.ratelimit.middleware as mw
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)
from src.core.ratelimit.storage import MemoryStorage


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Start every test with cold, independent caches."""
    rk.clear_canonicalize_cache()
    mw._reset_unknown_route_cache()
    yield
    rk.clear_canonicalize_cache()
    mw._reset_unknown_route_cache()


def _uncached_canonical(route):
    """Reference result straight from the middleware, bypassing the memo."""
    match = rk._ROUTE_WITH_METHOD_RE.match(route)
    if match is None:
        return route
    canonical, _, _ = mw.extract_route_info(match.group(2), match.group(1))
    return canonical or route


INPUTS = [
    "GET /users/{id}",
    "POST /auth/login",
    "GET /channels/{id}/messages",
    "POST /api/v1/channels/12345/messages",
    "GET /api/v1/channels/999/messages?x=1",
    "decorator:POST /api/v1/media/upload",
    "/api/v1/auth/login",  # no method prefix -> pass through
    "THUMBNAIL_GEN",  # symbolic -> pass through
]


def test_canonicalization_matches_uncached_reference():
    """Memo hits/misses produce byte-identical keys to direct normalization."""
    first = {route: rk.canonicalize_route(route) for route in INPUTS}
    # Force fresh misses in a different order, then warm hits again.
    rk.clear_canonicalize_cache()
    second = {route: rk.canonicalize_route(route) for route in reversed(INPUTS)}
    third = {route: rk.canonicalize_route(route) for route in INPUTS}
    assert first == second == third
    for route in INPUTS:
        assert first[route] == _uncached_canonical(route)


def test_non_string_routes_pass_through_uncached():
    """Unhashable / non-string callers never reach the memo."""
    for value in (None, 123, ["GET", "/x"], {"route": "GET /x"}):
        assert rk.canonicalize_route(value) is value
    assert rk._canonicalize_string.cache_info().currsize == 0


def test_repeated_route_avoids_redundant_route_table_scan(monkeypatch):
    """A warm route does not re-enter extract_route_info (or its lock)."""
    calls = []
    original = mw.extract_route_info

    def counting(path, method, *args, **kwargs):
        calls.append((path, method))
        return original(path, method, *args, **kwargs)

    monkeypatch.setattr(mw, "extract_route_info", counting)
    route = "GET /channels/{id}/messages"
    for _ in range(50):
        assert rk.canonicalize_route(route) == route
    assert len(calls) == 1, f"route table re-scanned {len(calls)} times"


def test_memo_is_bounded_under_raw_path_flood():
    """Unbounded attacker-supplied paths cannot grow the memo without limit."""
    for i in range(rk.CANONICALIZE_CACHE_MAXSIZE + 500):
        rk.canonicalize_route(f"GET /api/v1/flood/{i}/detail")
    assert (
        rk._canonicalize_string.cache_info().currsize <= rk.CANONICALIZE_CACHE_MAXSIZE
    )


def test_prefix_change_invalidates_memo(monkeypatch):
    """A changed API prefix moves the cache key instead of serving a stale route."""
    monkeypatch.setattr(rk, "_api_prefix_token", lambda: "/api/v1")
    old = rk.canonicalize_route("GET /api/v1/channels/123/messages")
    monkeypatch.setattr(rk, "_api_prefix_token", lambda: "/api/v2")
    new = rk.canonicalize_route("GET /api/v1/channels/123/messages")
    # Both paths agree for the default-prefix-shaped input; what matters is
    # that a new cache entry was created for the new prefix token.
    assert rk._canonicalize_string.cache_info().currsize == 2
    assert old == new == "GET /channels/{id}/messages"


def _manager(shared_storage, route, limit):
    cfg = RateLimitConfig(
        requests=limit,
        window_seconds=60.0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.ROUTE,
    )
    high = RateLimitConfig(requests=100000, window_seconds=60.0)
    return RateLimitManager(
        storage_backend=shared_storage,
        route_configs={route: cfg},
        global_config=high,
        ip_config=high,
        enable_global_limit=False,
    )


def test_two_workers_derive_identical_keys_and_share_quota():
    """Independent worker caches must not change keys or the shared budget."""
    canonical = rk.canonicalize_route("POST /api/v1/channels/111/messages")
    assert canonical == "POST /channels/{id}/messages"
    shared = MemoryStorage()
    worker_a = _manager(shared, canonical, limit=2)
    worker_b = _manager(shared, canonical, limit=2)

    # Worker A spends one unit using raw path id 111.
    a1 = worker_a.check_rate_limit(
        ip_address="9.9.9.9", route="POST /api/v1/channels/111/messages"
    )
    # Simulate a different process: cold its local memo, use a different id.
    rk.clear_canonicalize_cache()
    b1 = worker_b.check_rate_limit(
        ip_address="9.9.9.9", route="POST /api/v1/channels/222/messages"
    )

    assert a1.allowed and b1.allowed
    assert a1.bucket_key == b1.bucket_key, "workers derived different bucket keys"

    # The shared route bucket is now exhausted: both workers must deny.
    rk.clear_canonicalize_cache()
    a2 = worker_a.check_rate_limit(
        ip_address="9.9.9.9", route="POST /api/v1/channels/333/messages"
    )
    assert not a2.allowed
    assert a2.bucket_key == a1.bucket_key


def test_concurrent_workers_never_exceed_shared_limit():
    """Cache clears interleaved with checks cannot inflate the global budget."""
    canonical = rk.canonicalize_route("POST /api/v1/channels/1/messages")
    shared = MemoryStorage()
    limit = 3
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def run(i):
        try:
            barrier.wait()
            rk.clear_canonicalize_cache()  # independent per-worker cache state
            worker = _manager(shared, canonical, limit)
            result = worker.check_rate_limit(
                ip_address="8.8.8.8",
                route=f"POST /api/v1/channels/{i}/messages",
            )
            results.append(result)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sum(1 for result in results if result.allowed) == limit
    assert len({result.bucket_key for result in results}) == 1


def test_fail_closed_preserved_with_cached_route():
    """A broken store still denies even when canonicalization is memoized."""

    class BrokenStorage(MemoryStorage):
        def update_bucket(self, *args, **kwargs):
            raise RuntimeError("store down")

    canonical = rk.canonicalize_route("GET /users/{id}")
    worker = _manager(BrokenStorage(), canonical, limit=5)
    route = "GET /api/v1/users/123"
    assert not worker.check_rate_limit(ip_address="7.7.7.7", route=route).allowed
    # Warm the memo, then check again: still a fail-closed denial.
    rk.canonicalize_route(route)
    assert not worker.check_rate_limit(ip_address="7.7.7.7", route=route).allowed
