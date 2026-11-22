"""Regression: the ASGI rate-limit middleware resolves route info once per
(distinct path, method, api_prefix) instead of scanning the compiled route
table -- and contending on the unknown-route lock -- on every request.

``extract_route_info`` is a pure function of those three inputs but the
middleware called it directly on the hot path. The manager boundary already
memoizes its own re-canonicalization; this pins the missing first half. The
memo is bounded because the raw ASGI path is attacker-controlled, and the API
prefix is part of the key so a config change is observed immediately.

Before: N identical requests -> N calls into ``extract_route_info``.
After:  N identical requests -> 1 call (the rest are memo hits).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import ratelimit
from src.core.ratelimit import middleware as mw
from src.core.ratelimit import RateLimitMiddleware
from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.storage import MemoryStorage


@pytest.fixture(autouse=True)
def _clean_route_info_memo():
    """Isolate the bounded memo around every test in this module."""
    mw.clear_route_info_cache()
    yield
    mw.clear_route_info_cache()


class TestRouteInfoMemo:
    def test_repeated_lookups_collapse_to_one_extraction(self, monkeypatch):
        calls = []
        real = mw.extract_route_info

        def counting(path, method):
            calls.append((path, method))
            return real(path, method)

        monkeypatch.setattr(mw, "extract_route_info", counting)

        path = "/api/v1/channels/12345/messages"
        results = [mw.route_info_for_request(path, "POST") for _ in range(50)]

        assert len(calls) == 1, "warm lookups must not re-enter extract_route_info"
        assert all(r == results[0] for r in results)
        assert results[0][0] == "POST /channels/{id}/messages"
        assert results[0][1] == 12345
        assert results[0][2] is None

    def test_cache_is_bounded_under_distinct_path_flood(self):
        """A crafted flood of distinct paths cannot grow the memo without limit."""
        cap = mw.ROUTE_INFO_CACHE_MAXSIZE
        for i in range(cap + 256):
            mw.route_info_for_request(f"/api/v1/channels/{i}/messages", "POST")

        info = mw._extract_route_info_cached.cache_info()
        assert info.currsize <= cap
        # The memo is actually doing work, not silently disabled.
        assert info.currsize > 0

    def test_key_partitions_by_method_and_prefix(self, monkeypatch):
        path = "/api/v1/channels/12345/messages"
        post = mw.route_info_for_request(path, "POST")
        get = mw.route_info_for_request(path, "GET")
        assert post[0] == "POST /channels/{id}/messages"
        assert get[0] == "GET /channels/{id}/messages"

        # A renamed prefix must move the key so the new route table is used.
        monkeypatch.setattr(mw, "_resolve_api_prefix", lambda: "/api/v2")
        v2 = mw.route_info_for_request("/api/v2/channels/12345/messages", "POST")
        assert v2[0] == "POST /channels/{id}/messages"
        assert v2[1] == 12345

    def test_distinct_ids_keep_distinct_resource_ids(self):
        """Memo hits never leak one request's resource id into another's."""
        a = mw.route_info_for_request("/api/v1/channels/111/messages", "POST")
        b = mw.route_info_for_request("/api/v1/channels/222/messages", "POST")
        assert a[1] == 111 and b[1] == 222
        assert a[2] is None and b[2] is None


class TestMiddlewareUsesMemo:
    @pytest.fixture
    def app_with_ratelimit(self):
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=100,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_each_request_uses_the_memoized_resolver(
        self, app_with_ratelimit, monkeypatch
    ):
        """The middleware call site goes through the memoized resolver."""
        calls = []
        real = mw.route_info_for_request

        def counting(path, method):
            calls.append((path, method))
            return real(path, method)

        monkeypatch.setattr(mw, "route_info_for_request", counting)

        client = TestClient(app_with_ratelimit)
        for _ in range(5):
            assert client.get("/api/v1/test").status_code == 200

        assert len(calls) == 5, "one route-info resolution per request"
        assert all(c == ("/api/v1/test", "GET") for c in calls)
