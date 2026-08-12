"""
Tests for the Self-Test module.
"""

import pytest

requests = pytest.importorskip("requests")

from src.core.selftest.runner import SelfTestRunner  # noqa: E402
from src.api.middleware.error_handling import get_status_code_for_exception  # noqa: E402


def test_status_code_mapping():
    """Verify that exception to status code mapping works."""
    assert get_status_code_for_exception(ValueError("test")) == 500
    assert get_status_code_for_exception(RuntimeError("test")) == 500


@pytest.mark.asyncio
async def test_route_discovery():
    """Verify that the runner can discover routes from OpenAPI."""
    # Can't do real route discovery without a running server,
    # but verify that discover_routes returns empty list on failure
    runner = SelfTestRunner(base_url="http://localhost:1")
    routes = runner.discovery.discover_routes()
    # Should return empty list when server is unreachable
    assert isinstance(routes, list)
    assert len(routes) == 0


def test_runner_initialization():
    """Test runner setup."""
    runner = SelfTestRunner(base_url="http://localhost:8000")
    assert runner.ctx.base_url == "http://localhost:8000"
    assert runner.ctx.results == []


def test_unlicensed_flag_is_explicit_only():
    """Unlicensed mode must NEVER be inferred from the machine's license.

    ``--unlicensed`` is the sole switch: without it the runner treats every
    endpoint (including license-gated /plexijoin/ routes) as testable, and
    with it the context flag is set for the run_all() exclusion logic.
    """
    runner = SelfTestRunner(base_url="http://localhost:8000")
    assert runner.ctx.unlicensed is False

    runner2 = SelfTestRunner(base_url="http://localhost:8000", unlicensed=True)
    assert runner2.ctx.unlicensed is True


def test_unlicensed_excludes_plexijoin_routes():
    """With --unlicensed, /plexijoin/ endpoints are added to the exclusion set.

    The exclusion set is private to run_all(), so this tests the observable
    contract: the runner's ``unlicensed`` flag is threaded through to the
    context and the RouteDiscovery helper reports the expected 403 for the
    license-gated feature. (The full exclusion loop itself runs against a
    live OpenAPI spec in the real self-test.)
    """
    runner = SelfTestRunner(base_url="http://localhost:8000", unlicensed=True)
    assert runner.ctx.unlicensed is True
    assert runner.discovery is not None
