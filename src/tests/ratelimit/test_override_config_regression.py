"""Regression tests for the ``override_config`` plumbing on the async path.

``@rate_limit`` / ``@custom_rate_limit`` used to publish their per-route limit
by mutating the manager's shared ``_route_configs`` dict and popping the key in
``finally``, while ``acheck_rate_limit`` read that dict *after* earlier awaits.
An overlapping request to the same decorated route could therefore find the key
already removed and fall back to the generic default (60 requests / 30 s).

The fix passes the config explicitly: ``RateLimitManager.acheck_rate_limit`` and
the package wrapper ``src.core.ratelimit.acheck_rate_limit`` both accept an
optional ``override_config``, which ``_get_config_for_request`` prefers over the
shared route map. These tests pin that down so the regression cannot return.
"""

import inspect

import pytest

import src.core.ratelimit as ratelimit
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage


def _manager(route_configs=None) -> RateLimitManager:
    """Manager with no global bucket; only the routes passed in are registered."""
    return RateLimitManager(
        storage_backend=MemoryStorage(),
        route_configs=route_configs or {},
        enable_global_limit=False,
    )


@pytest.fixture
def rl_state():
    """Save/restore the global ratelimit module state around a test."""
    prev_manager = ratelimit._manager
    prev_complete = ratelimit._setup_complete
    try:
        yield ratelimit
    finally:
        ratelimit._manager = prev_manager
        ratelimit._setup_complete = prev_complete


class TestManagerOverrideConfig:
    """The manager must prefer ``override_config`` over the shared route map."""

    @pytest.mark.asyncio
    async def test_override_config_governs_an_unregistered_route(self):
        # "GET /probe" is deliberately NOT in _route_configs, so without the
        # override it would use the 60/30s default and admit both calls.
        manager = _manager()
        override = RateLimitConfig(requests=1, window_seconds=3600.0, burst=0)

        first = await manager.acheck_rate_limit(
            user_id=7, route="GET /probe", override_config=override
        )
        second = await manager.acheck_rate_limit(
            user_id=7, route="GET /probe", override_config=override
        )

        assert first.allowed is True
        assert second.allowed is False

    @pytest.mark.asyncio
    async def test_override_config_beats_a_registered_route_config(self):
        # The route is registered with a generous limit; the explicit override
        # (requests=1) must still win.
        registered = RateLimitConfig(requests=100, window_seconds=60.0, burst=0)
        manager = _manager({"GET /probe": registered})
        override = RateLimitConfig(requests=1, window_seconds=3600.0, burst=0)

        first = await manager.acheck_rate_limit(
            user_id=9, route="GET /probe", override_config=override
        )
        second = await manager.acheck_rate_limit(
            user_id=9, route="GET /probe", override_config=override
        )

        assert first.allowed is True
        assert second.allowed is False

    @pytest.mark.asyncio
    async def test_no_override_falls_back_to_the_default(self):
        # Backward compatibility: omitting the override keeps prior behaviour,
        # so two calls against the unregistered route are both admitted.
        manager = _manager()
        for _ in range(2):
            result = await manager.acheck_rate_limit(user_id=8, route="GET /probe")
            assert result.allowed is True


class TestPackageWrapperOverrideConfig:
    """The public wrapper must accept and forward ``override_config``."""

    def test_wrapper_signature_accepts_override_config(self):
        params = inspect.signature(ratelimit.acheck_rate_limit).parameters
        assert "override_config" in params
        # Optional with a ``None`` default, so existing callers keep binding.
        assert params["override_config"].default is None

    @pytest.mark.asyncio
    async def test_wrapper_forwards_override_config(self, rl_state):
        rl = rl_state
        rl._manager = None
        rl._setup_complete = False
        assert rl.setup(storage_backend=MemoryStorage(), enable_global_limit=False)

        override = RateLimitConfig(requests=1, window_seconds=3600.0, burst=0)
        first = await rl.acheck_rate_limit(
            user_id=99, route="GET /probe", override_config=override
        )
        second = await rl.acheck_rate_limit(
            user_id=99, route="GET /probe", override_config=override
        )

        # A wrapper that dropped the kwarg would either raise TypeError or let
        # the second call through on the default limit; neither may happen.
        assert first.allowed is True
        assert second.allowed is False
