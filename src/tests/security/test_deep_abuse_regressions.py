"""End-to-end abuse regressions: rotation, outages and concurrent bursts.

The individual hardening layers each have focused tests; these scenarios drive
the ASGI middleware the way an attacker would and assert the observable
outcome: a rotated header cannot refresh a bucket, a store outage cannot turn
into a free pass, and a concurrent burst cannot spend more than the limit.
"""

import asyncio
from typing import Any, Dict, List

import pytest
from starlette.requests import Request

from src.api.middleware import rate_limiting as rl_middleware
from src.core import ratelimit
from src.core.ratelimit import middleware as core_middleware
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage

API_PREFIX = "/api/v1"


class _User:
    user_id = 42
    token_type = ""
    permissions: Dict[str, bool] = {}


def _scope(headers: List[tuple], client: str = "10.0.0.1") -> Dict[str, Any]:
    return {
        "type": "http",
        "path": f"{API_PREFIX}/channels/123/messages",
        "method": "POST",
        "headers": headers,
        "query_string": b"",
        "client": (client, 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }


def _config(requests: int) -> RateLimitConfig:
    return RateLimitConfig(
        requests=requests,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )


def _manager(storage: Any, requests: int) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"POST /channels/{id}/messages": _config(requests)},
        enable_global_limit=False,
    )


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, manager: RateLimitManager) -> None:
    monkeypatch.setattr(ratelimit, "_manager", manager, raising=False)
    monkeypatch.setattr(ratelimit, "_setup_complete", True, raising=False)

    def fake_config(key: str, default: Any = None) -> Any:
        if key == "rate_limiting":
            return {"bypass_secret": "s3cr3t"}
        if key == "api":
            return {"api_prefix": API_PREFIX}
        return {} if default is None else default

    monkeypatch.setattr(rl_middleware.config, "get", fake_config)
    monkeypatch.setattr(rl_middleware, "cached_is_self_test", lambda request: False)
    monkeypatch.setattr(
        core_middleware,
        "get_client_ip",
        lambda request: request.headers.get("x-forwarded-for", "10.0.0.1"),
    )


def _authenticated_info(request: Request) -> Dict[str, Any]:
    request.state.user = _User()
    return rl_middleware.get_user_info_from_request(request)


def _middleware_factory():
    return lambda app: core_middleware.RateLimitMiddlewareASGI(
        app, get_user_info=_authenticated_info
    )


async def _run(scope: Dict[str, Any], middleware_factory: Any) -> bool:
    """Drive one request; True means the downstream app was reached."""
    reached: List[bool] = []

    async def downstream(scope, receive, send):
        reached.append(True)

    async def receive() -> Dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Dict[str, Any]) -> None:
        return None

    await middleware_factory(downstream)(scope, receive, send)
    return bool(reached)


class FlippableStorage(MemoryStorage):
    """Storage that can be taken offline mid-burst."""

    def __init__(self) -> None:
        super().__init__()
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise ConnectionError("store offline")

    def update_bucket(self, *args: Any, **kwargs: Any) -> None:
        self._check()
        return super().update_bucket(*args, **kwargs)

    async def aupdate_bucket(self, *args: Any, **kwargs: Any) -> None:
        self._check()
        return await super().aupdate_bucket(*args, **kwargs)

    def eval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        self._check()
        return super().eval_token_bucket(*args, **kwargs)

    async def aeval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        self._check()
        return await super().aeval_token_bucket(*args, **kwargs)


class TestHeaderRotationAbuse:
    @pytest.mark.asyncio
    async def test_rotated_forwarded_and_bypass_headers_cannot_refresh_bucket(
        self, monkeypatch
    ):
        storage = MemoryStorage()
        manager = _manager(storage, requests=3)
        _patch_runtime(monkeypatch, manager)
        factory = _middleware_factory()

        allowed = 0
        for index in range(40):
            scope = _scope(
                [
                    (b"x-forwarded-for", f"10.0.0.{index}".encode()),
                    (b"x-ratelimit-bypass", f"guess-{index}".encode()),
                ]
            )
            if await _run(scope, factory):
                allowed += 1

        assert allowed == 3
        assert storage.get_stats()["bucket_count"] <= 2

    def test_bypass_secret_guesses_are_not_constant_time_equal(self, monkeypatch):
        _patch_runtime(monkeypatch, _manager(MemoryStorage(), requests=3))

        for guess in (b"", b"s3cr", b"S3CRET", b"x" * 64):
            request = Request(
                _scope([(b"x-ratelimit-bypass", guess)]),
                lambda: asyncio.sleep(0),
            )
            info = rl_middleware.get_user_info_from_request(request)
            assert info["is_internal"] is False

        request = Request(
            _scope([(b"x-ratelimit-bypass", b"s3cr3t")]),
            lambda: asyncio.sleep(0),
        )
        assert rl_middleware.get_user_info_from_request(request)["is_internal"] is True


class TestStoreOutageAbuse:
    @pytest.mark.asyncio
    async def test_outage_mid_burst_denies_without_500(self, monkeypatch):
        storage = FlippableStorage()
        manager = _manager(storage, requests=5)
        _patch_runtime(monkeypatch, manager)
        factory = _middleware_factory()

        assert sum([await _run(_scope([]), factory) for _ in range(3)]) == 3

        storage.available = False
        for _ in range(10):
            assert await _run(_scope([]), factory) is False

        storage.available = True
        assert sum([await _run(_scope([]), factory) for _ in range(5)]) == 2


class TestConcurrentBurstAbuse:
    @pytest.mark.asyncio
    async def test_parallel_requests_spend_limit_exactly_once(self, monkeypatch):
        storage = MemoryStorage()
        manager = _manager(storage, requests=4)
        _patch_runtime(monkeypatch, manager)
        factory = _middleware_factory()
        scopes = [
            _scope([(b"x-forwarded-for", f"172.16.0.{index}".encode())])
            for index in range(40)
        ]
        outcomes = await asyncio.gather(*[_run(scope, factory) for scope in scopes])

        assert sum(outcomes) == 4
