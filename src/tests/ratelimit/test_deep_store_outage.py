"""Regression: a rate-limit store outage must never become a free pass.

Every limiter path -- sync/async, token bucket/legacy algorithms and the ASGI
middleware itself -- has to deny when the backend cannot answer. Previously an
exception either escaped as a 500 or, for backends that swallowed it, left the
outcome dictionary empty and raised ``KeyError`` on the way to a response.
"""

from typing import Any, Dict, List

import pytest

from src.core import ratelimit
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.middleware import RateLimitMiddlewareASGI
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage


def _config(algorithm: RateLimitAlgorithm) -> RateLimitConfig:
    return RateLimitConfig(
        requests=2,
        window_seconds=60.0,
        burst=0,
        algorithm=algorithm,
    )


def _manager(storage: MemoryStorage, algorithm: RateLimitAlgorithm) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /t": _config(algorithm)},
        enable_global_limit=False,
    )


class OutageStorage(MemoryStorage):
    """Storage that fails every mutating/evaluating call."""

    def eval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        raise ConnectionError("valkey down")

    async def aeval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        raise ConnectionError("valkey down")

    def update_bucket(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey down")

    async def aupdate_bucket(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey down")


class SilentStorage(MemoryStorage):
    """Storage that silently drops mutations, as an unavailable backend does."""

    def update_bucket(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aupdate_bucket(self, *args: Any, **kwargs: Any) -> None:
        return None


def _assert_denied(result: Any) -> None:
    assert result.allowed is False
    assert result.retry_after is not None and result.retry_after >= 1
    headers = result.headers.to_dict()
    assert headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in headers
    assert headers["X-RateLimit-Scope"] == "store"


class TestStoreOutageFailsClosed:
    @pytest.mark.parametrize(
        "algorithm",
        [RateLimitAlgorithm.TOKEN_BUCKET, RateLimitAlgorithm.SLIDING_WINDOW],
    )
    def test_sync_check_denies_when_store_raises(self, algorithm):
        manager = _manager(OutageStorage(), algorithm)

        _assert_denied(manager.check_rate_limit(user_id=1, route="GET /t"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "algorithm",
        [RateLimitAlgorithm.TOKEN_BUCKET, RateLimitAlgorithm.SLIDING_WINDOW],
    )
    async def test_async_check_denies_when_store_raises(self, algorithm):
        manager = _manager(OutageStorage(), algorithm)

        _assert_denied(await manager.acheck_rate_limit(user_id=1, route="GET /t"))

    def test_silent_store_denies_instead_of_crashing(self, memory_storage):
        manager = _manager(SilentStorage(), RateLimitAlgorithm.SLIDING_WINDOW)

        _assert_denied(manager.check_rate_limit(user_id=1, route="GET /t"))

    @pytest.mark.asyncio
    async def test_middleware_returns_429_and_skips_app_on_limiter_failure(
        self, memory_storage, monkeypatch
    ):
        manager = _manager(OutageStorage(), RateLimitAlgorithm.SLIDING_WINDOW)
        monkeypatch.setattr(ratelimit, "_manager", manager, raising=False)
        monkeypatch.setattr(ratelimit, "_setup_complete", True, raising=False)

        called: List[bool] = []

        async def downstream(scope, receive, send):
            called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        messages: List[Dict[str, Any]] = []

        async def send(message: Dict[str, Any]) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "path": "/api/v1/t",
            "method": "GET",
            "headers": [],
            "query_string": b"",
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
        middleware = RateLimitMiddlewareASGI(
            downstream,
            get_user_info=lambda request: {
                "user_id": 1,
                "ip_address": "10.0.0.1",
                "is_bot": False,
                "is_admin": False,
                "is_internal": False,
                "is_webhook": False,
                "webhook_id": None,
            },
        )

        await middleware(scope, receive, send)

        assert called == []
        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 429
        header_names = {name.lower() for name, _ in start["headers"]}
        assert b"retry-after" in header_names
        assert b"x-ratelimit-limit" in header_names

    @pytest.mark.asyncio
    async def test_middleware_denies_on_unexpected_decision_error(
        self, memory_storage, monkeypatch
    ):
        monkeypatch.setattr(
            ratelimit,
            "_manager",
            _manager(memory_storage, RateLimitAlgorithm.SLIDING_WINDOW),
            raising=False,
        )
        monkeypatch.setattr(ratelimit, "_setup_complete", True, raising=False)

        async def boom(**kwargs: Any) -> Any:
            raise RuntimeError("limiter decision exploded")

        monkeypatch.setattr(ratelimit, "acheck_rate_limit", boom)

        called: List[bool] = []

        async def downstream(scope, receive, send):
            called.append(True)

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        messages: List[Dict[str, Any]] = []

        async def send(message: Dict[str, Any]) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "path": "/api/v1/t",
            "method": "GET",
            "headers": [],
            "query_string": b"",
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
        await RateLimitMiddlewareASGI(downstream)(scope, receive, send)

        assert called == []
        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 429
