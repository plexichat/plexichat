"""DSAR export throttle: 1 request/user/day via the shared rate limiter.

Pins the ``custom_rate_limit`` decorator on the export route:
- enforcement: a second request inside the window raises 429 with
  standard rate-limit headers;
- self-test bypass: internal requests (flagged by AuthenticationMiddleware)
  never count, so the suite stays re-runnable.
"""

import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from src.core import ratelimit
from src.core.ratelimit.decorators import custom_rate_limit
from src.core.ratelimit.models import RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage


@pytest.fixture
def limiter():
    ratelimit.setup(storage_backend=MemoryStorage(), enable_global_limit=False)
    yield ratelimit.get_manager()
    try:
        ratelimit.reset_all()
    except Exception:
        pass


def _make_request(user_id, is_internal):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/users/@me/data-export",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    req = Request(scope)
    req.state.user = SimpleNamespace(user_id=user_id, token_type="user", permissions={})
    req.state.is_internal = is_internal
    return req


def _export_stub():
    @custom_rate_limit(config=RateLimitConfig(requests=1, window_seconds=86400))
    async def stub(request: Request):
        return {"ok": True}

    return stub


def _uid():
    return int(time.time() * 1000) % 1_000_000_000 + uuid.uuid4().int % 997


@pytest.mark.asyncio
async def test_second_request_inside_window_429s(limiter):
    stub = _export_stub()
    uid = _uid()
    assert await stub(_make_request(uid, False)) == {"ok": True}
    with pytest.raises(HTTPException) as exc_info:
        await stub(_make_request(uid, False))
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert "X-RateLimit-Remaining" in (exc_info.value.headers or {})


@pytest.mark.asyncio
async def test_internal_requests_never_count(limiter):
    stub = _export_stub()
    uid = _uid()
    for _ in range(3):
        assert await stub(_make_request(uid, True)) == {"ok": True}


@pytest.mark.asyncio
async def test_middleware_route_charge_does_not_eat_the_daily_budget(limiter):
    """The generic per-route bucket must not be the decorator's bucket.

    ``RateLimitMiddleware`` charges ``<METHOD> <path>`` (as named by
    ``extract_route_info``) before the handler runs. The decorator used that
    exact string as its route, so one request recorded two hits in a single
    sliding-window state: the middleware's generic cap passed, then the
    1/day check found the budget spent and 429'd the user's very first
    export. Worse, whichever window is shorter prunes the other's history,
    so neither cap was trustworthy.
    """
    from src.core.ratelimit.middleware import extract_route_info

    stub = _export_stub()
    uid = _uid()
    request = _make_request(uid, False)
    middleware_route, _, _ = extract_route_info(request.url.path, request.method)

    pre = await limiter.acheck_rate_limit(user_id=uid, route=middleware_route)
    assert pre.allowed

    assert await stub(_make_request(uid, False)) == {"ok": True}


@pytest.mark.asyncio
async def test_throttle_is_per_user(limiter):
    stub = _export_stub()
    assert await stub(_make_request(_uid(), False)) == {"ok": True}
    # A different user has their own daily budget.
    assert await stub(_make_request(_uid(), False)) == {"ok": True}
