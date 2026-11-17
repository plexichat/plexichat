"""Deterministic rate-limit security invariants.

Two properties are pinned here:

* when the backing store cannot answer, every decision path denies (fails
  closed) instead of handing out a free pass or bubbling a 500;
* a client cannot escape its bucket by rotating the identity inputs that feed
  the key (a forged user id, ``X-Forwarded-For`` values, or garbage strings).

All tests use the in-memory storage with deterministic doubles -- no live
Valkey, no wall-clock sleeps.
"""

from typing import Any, Dict, List

import pytest

from src.core import ratelimit
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.manager.constants import MAX_TOTAL_KEY_LENGTH
from src.core.ratelimit.middleware import RateLimitMiddlewareASGI
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)
from src.core.ratelimit.storage import MemoryStorage

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.ratelimit]


def _config() -> RateLimitConfig:
    return RateLimitConfig(
        requests=2,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )


def _manager(storage: MemoryStorage) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /t": _config()},
        ip_config=_config(),
        enable_global_limit=False,
    )


class _OutageStorage(MemoryStorage):
    """Storage that fails on every mutating / evaluating call."""

    def eval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        raise ConnectionError("store down")

    async def aeval_token_bucket(self, *args: Any, **kwargs: Any) -> tuple:
        raise ConnectionError("store down")

    def update_bucket(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("store down")

    async def aupdate_bucket(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("store down")


def _assert_store_denied(result: Any) -> None:
    assert result.allowed is False
    assert result.retry_after is not None and result.retry_after >= 1
    headers = result.headers.to_dict()
    assert headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in headers
    assert headers["X-RateLimit-Scope"] == "store"


# --- 1. fail closed on store outage ----------------------------------------


@pytest.mark.parametrize(
    "algorithm",
    [RateLimitAlgorithm.TOKEN_BUCKET, RateLimitAlgorithm.SLIDING_WINDOW],
)
def test_sync_decision_denies_when_store_raises(algorithm):
    manager = RateLimitManager(
        storage_backend=_OutageStorage(),
        route_configs={
            "GET /t": RateLimitConfig(
                requests=2,
                window_seconds=60.0,
                burst=0,
                algorithm=algorithm,
            )
        },
        enable_global_limit=False,
    )
    _assert_store_denied(manager.check_rate_limit(user_id=1, route="GET /t"))


@pytest.mark.asyncio
async def test_async_decision_denies_when_store_raises():
    manager = _manager(_OutageStorage())
    _assert_store_denied(await manager.acheck_rate_limit(user_id=1, route="GET /t"))


@pytest.mark.asyncio
async def test_middleware_returns_429_and_skips_app_on_store_outage(monkeypatch):
    monkeypatch.setattr(
        ratelimit, "_manager", _manager(_OutageStorage()), raising=False
    )
    monkeypatch.setattr(ratelimit, "_setup_complete", True, raising=False)

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


@pytest.mark.asyncio
async def test_middleware_denies_on_unexpected_decision_error(monkeypatch):
    monkeypatch.setattr(ratelimit, "_manager", _manager(MemoryStorage()), raising=False)
    monkeypatch.setattr(ratelimit, "_setup_complete", True, raising=False)

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("limiter exploded")

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


# --- 2. cannot bypass via principal rotation -------------------------------


def test_authenticated_principal_survives_ip_rotation(memory_storage):
    manager = _manager(memory_storage)
    first = manager.check_rate_limit(user_id=7, ip_address="10.0.0.1", route="GET /t")
    second = manager.check_rate_limit(user_id=7, ip_address="10.0.0.2", route="GET /t")
    assert first.bucket_key == second.bucket_key
    assert memory_storage.get_keys_by_prefix("ip:") == []
    # The bucket has a capacity of two; a third request is denied regardless
    # of the IP the client presents.
    assert (
        manager.check_rate_limit(
            user_id=7, ip_address="10.0.0.3", route="GET /t"
        ).allowed
        is False
    )


def test_rotating_garbage_ips_cannot_grow_the_keyspace(memory_storage):
    manager = _manager(memory_storage)
    seen = set()
    for index in range(300):
        result = manager.check_rate_limit(
            ip_address=f"attacker-{index}-" + "x" * 200, route="GET /t"
        )
        seen.add(result.bucket_key)
    assert len(seen) <= 2
    assert memory_storage.get_stats()["bucket_count"] <= 2


def test_forged_user_id_cannot_inject_key_segments(memory_storage):
    manager = _manager(memory_storage)
    result = manager.check_rate_limit(
        user_id="1:u:2", ip_address="10.0.0.1", route="GET /t"
    )
    assert ":u:" not in result.bucket_key

    forged = manager._generate_bucket_key(
        BucketType.ROUTE, user_id="1:u:2", route="GET /t"
    )
    assert "u:1%3Au%3A2" in forged
    assert len(forged) <= MAX_TOTAL_KEY_LENGTH


@pytest.mark.parametrize("bad_id", [True, -1, 1.5, "", " 12x ", object()])
def test_malformed_ids_fall_back_to_ip_not_a_fresh_bucket(memory_storage, bad_id):
    manager = _manager(memory_storage)
    result = manager.check_rate_limit(
        user_id=bad_id, ip_address="10.0.0.1", route="GET /t"
    )
    assert "u:" not in result.bucket_key
    assert ":ip:10.0.0.1" in result.bucket_key


def test_digit_string_and_int_are_the_same_principal(memory_storage):
    manager = _manager(memory_storage)
    assert manager.check_rate_limit(user_id="007", route="GET /t").bucket_key == (
        manager.check_rate_limit(user_id=7, route="GET /t").bucket_key
    )
