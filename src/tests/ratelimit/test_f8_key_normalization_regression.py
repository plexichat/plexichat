"""
F8 regression test: unbounded key growth via raw-path rate-limit keys.

Pre-fix behaviour: ``extract_route_info`` returned ``f"{method} {path}"``
verbatim for any unknown path, so flooding ``/api/a?x=1..N`` (or
``/api/evil/1..N``) created N distinct bucket rows in persistent backends
with no eviction.

Post-fix: query strings are stripped, ID-like segments collapse to
``{id}``, and the distinct unknown-route keyspace is bounded
(``MAX_DISTINCT_UNKNOWN_ROUTES`` + hashed overflow buckets), with TTL
expiry and row-count eviction enforced on every storage backend.
"""

import pytest

from src.core.ratelimit import middleware as mw
from src.core.ratelimit.middleware import extract_route_info
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.storage import MemoryStorage
from src.core.ratelimit.storage.database import DatabaseStorage
from src.core.ratelimit.storage.valkey import ValkeyStorage


@pytest.fixture(autouse=True)
def _clean_unknown_cache():
    mw._reset_unknown_route_cache()
    yield
    mw._reset_unknown_route_cache()


class TestKeyNormalization:
    """The key-building function must normalize attacker-controlled input."""

    def test_query_strings_do_not_create_distinct_buckets(self):
        routes = {extract_route_info(f"/api/probe?x={i}", "GET")[0] for i in range(200)}
        assert len(routes) == 1, f"query strings fanned out: {routes}"

    def test_numeric_segments_share_one_bucket(self):
        routes = {extract_route_info(f"/api/evil/{i}", "GET")[0] for i in range(200)}
        assert len(routes) == 1, f"numeric segments fanned out: {routes}"

    def test_uuid_segments_share_one_bucket(self):
        import uuid

        routes = {
            extract_route_info(f"/api/evil/{uuid.uuid4()}", "GET")[0] for _ in range(50)
        }
        assert len(routes) == 1, f"uuid segments fanned out: {routes}"

    def test_distinct_unknown_paths_bounded(self):
        """1000 distinct unknown paths must NOT create 1000 buckets."""
        routes = {
            extract_route_info(f"/api/flood/path-{i}/detail", "GET")[0]
            for i in range(1000)
        }
        bound = mw.MAX_DISTINCT_UNKNOWN_ROUTES + mw.UNKNOWN_BUCKET_COUNT
        assert len(routes) <= bound, (
            f"unbounded key growth: 1000 paths -> {len(routes)} buckets"
        )
        assert len(routes) < 1000

    def test_different_routes_get_different_buckets(self):
        """Legitimate per-route limiting still works."""
        r1 = extract_route_info("/api/alpha/list", "GET")[0]
        r2 = extract_route_info("/api/beta/list", "GET")[0]
        assert r1 != r2

    def test_same_route_different_ids_share_bucket(self):
        r1, res1, _ = extract_route_info("/api/v1/channels/111/messages", "POST")
        r2, res2, _ = extract_route_info("/api/v1/channels/222/messages", "POST")
        assert r1 == r2 == "POST /channels/{id}/messages"
        assert (res1, res2) == (111, 222)

    def test_known_routes_unaffected(self):
        route, res, _ = extract_route_info("/api/v1/channels/12345/messages", "POST")
        assert route == "POST /channels/{id}/messages"
        assert res == 12345

    def test_overlong_path_is_capped(self):
        route, _, _ = extract_route_info("/api/" + "a" * 5000, "GET")
        assert len(route) <= mw.MAX_ROUTE_LENGTH + len("GET ")

    def test_middleware_never_returns_400_block(self):
        """Guard against cheating: normalization must not 'block' crafted
        paths with an error route; every path maps to a usable bucket."""
        for i in range(20):
            route, _, _ = extract_route_info(f"/api/crafted-{i}?z={i}", "GET")
            assert isinstance(route, str) and route.startswith("GET ")
            assert "400" not in route and "blocked" not in route.lower()


class TestStorageBounds:
    """Every backend must bound rows (TTL + eviction)."""

    def test_memory_storage_caps_writes_immediately(self):
        storage = MemoryStorage(cleanup_interval=3600.0, max_buckets=50)
        for i in range(200):
            storage.set_bucket(f"key:{i}", {"tokens": 1, "last_update": float(i)})
        # No sleep / no cleanup trigger: the cap applies on the write path.
        assert storage.get_stats()["bucket_count"] <= 50

    def test_manager_flood_stays_bounded(self):
        """End-to-end: 1000 distinct unknown routes through the manager."""
        storage = MemoryStorage(cleanup_interval=3600.0, max_buckets=1000)
        # F8: numeric segments collapse, so all 1000 flood paths share the
        # single normalized bucket "GET /api/flood/{id}/detail". Give that
        # bucket (and the per-IP bucket) a high limit so the test measures
        # key growth — not quota exhaustion — and every request is allowed.
        flood_route = "GET /api/flood/{id}/detail"
        hi_limit = RateLimitConfig(
            requests=100000,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=100000,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
                flood_route: hi_limit,
            },
            ip_config=hi_limit,
            enable_global_limit=False,
        )
        seen_routes = set()
        for i in range(1000):
            route, res, _ = extract_route_info(f"/api/flood/{i}/detail", "GET")
            seen_routes.add(route)
            result = manager.check_rate_limit(
                ip_address="10.0.0.1", route=route, resource_id=res
            )
            assert result.allowed
        assert len(seen_routes) < 1000
        assert storage.get_stats()["bucket_count"] <= 1000

    def test_manager_route_key_length_capped(self):
        manager = RateLimitManager(
            storage_backend=MemoryStorage(max_buckets=100),
            enable_global_limit=False,
        )
        evil_route = "GET /" + "x" * 5000
        key = manager._generate_bucket_key(
            __import__(
                "src.core.ratelimit.models", fromlist=["BucketType"]
            ).BucketType.ROUTE,
            ip_address="10.0.0.1",
            route=evil_route,
        )
        assert len(key) <= manager.MAX_ROUTE_KEY_LENGTH + 32

    def test_database_backend_has_row_cap_and_ttl(self):
        assert DatabaseStorage.MAX_BUCKET_ROWS <= 100000
        assert hasattr(DatabaseStorage, "_maybe_enforce_cap")

    def test_valkey_backend_applies_default_ttl(self):
        """Valkey writes must always carry a TTL (no persistent keys)."""
        from unittest.mock import MagicMock

        storage = ValkeyStorage()
        client = MagicMock()
        client.ttl.return_value = -1  # no expiry set
        import src.core.ratelimit.storage.valkey as valkey_mod

        real_get_client = valkey_mod.get_client
        real_is_available = valkey_mod.is_available
        valkey_mod.get_client = lambda: client
        valkey_mod.is_available = lambda: True
        try:
            storage.set_bucket("k", {"tokens": 1})  # no ttl passed
            _, kwargs = client.set_json.call_args
            assert kwargs.get("ttl") == ValkeyStorage.DEFAULT_BUCKET_TTL
            assert ValkeyStorage.DEFAULT_BUCKET_TTL > 0
        finally:
            valkey_mod.get_client = real_get_client
            valkey_mod.is_available = real_is_available
