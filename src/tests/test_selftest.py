"""
Tests for the Self-Test module.
"""

import pytest

requests = pytest.importorskip("requests")

from src.core.selftest.runner import SelfTestRunner
from src.api.middleware.error_handling import get_status_code_for_exception


def test_status_code_mapping():
    """Verify that exception to status code mapping works."""
    assert get_status_code_for_exception(ValueError("test")) == 500
    assert get_status_code_for_exception(RuntimeError("test")) == 500


@pytest.mark.asyncio
async def test_route_discovery():
    """Verify that the runner can discover routes from OpenAPI."""
    # Can't do real route discovery without a running server,
    # but verify that _discover_routes returns empty list on failure
    runner = SelfTestRunner(base_url="http://localhost:1")
    routes = runner._discover_routes()
    # Should return empty list when server is unreachable
    assert isinstance(routes, list)
    assert len(routes) == 0


def test_runner_initialization():
    """Test runner setup."""
    runner = SelfTestRunner(base_url="http://localhost:8000")
    assert runner.base_url == "http://localhost:8000"
    assert runner.results == []
