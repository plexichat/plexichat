"""Regression: a silently skipped secondary-limit write must fail closed.

``check_atomic_{sync,async}`` evaluates a token bucket with ``eval_token_bucket``
and then applies hourly/daily quotas with a second ``update_bucket`` call. A
backend that silently skips that second mutation (unreachable store, lock it
cannot acquire, swallowed exception) used to leave the secondary quota
unchecked and answer "allowed". That turned a store failure into a free pass
for any route carrying an hourly/daily limit. The outcome must only be trusted
when the mutate callback actually ran.
"""

from typing import Any

import pytest

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage


class SilentSecondaryStorage(MemoryStorage):
    """Reads/token-bucket work, but every bucket mutation is a silent no-op."""

    def update_bucket(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aupdate_bucket(self, *args: Any, **kwargs: Any) -> None:
        return None


def _config() -> RateLimitConfig:
    return RateLimitConfig(
        requests=10,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        hourly_limit=1,
    )


def _manager(storage: Any) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /t": _config()},
        enable_global_limit=False,
    )


class TestSecondaryLimitFailsClosed:
    def test_sync_silent_secondary_write_denies(self):
        manager = _manager(SilentSecondaryStorage())
        result = manager.check_rate_limit(user_id=1, route="GET /t")

        assert result.allowed is False
        assert result.retry_after is not None and result.retry_after >= 1
        assert result.headers.scope == "store"

    @pytest.mark.asyncio
    async def test_async_silent_secondary_write_denies(self):
        manager = _manager(SilentSecondaryStorage())
        result = await manager.acheck_rate_limit(user_id=1, route="GET /t")

        assert result.allowed is False
        assert result.retry_after is not None and result.retry_after >= 1
        assert result.headers.scope == "store"

    def test_healthy_secondary_limit_still_enforced(self):
        manager = _manager(MemoryStorage())
        assert manager.check_rate_limit(user_id=1, route="GET /t").allowed is True
        # The primary bucket has 10 tokens but the hourly quota is 1.
        assert manager.check_rate_limit(user_id=1, route="GET /t").allowed is False

    @pytest.mark.asyncio
    async def test_async_healthy_secondary_limit_still_enforced(self):
        manager = _manager(MemoryStorage())
        assert (await manager.acheck_rate_limit(user_id=1, route="GET /t")).allowed
        assert not (await manager.acheck_rate_limit(user_id=1, route="GET /t")).allowed
