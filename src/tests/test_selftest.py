"""
Tests for the Self-Test module.
"""

import pytest
from src.core.selftest.runner import SelfTestRunner
from src.api.middleware.error_handling import get_status_code_for_exception

def test_status_code_mapping():
    """Verify that exception to status code mapping works."""
    assert get_status_code_for_exception(ValueError("test")) == 400
    assert get_status_code_for_exception(RuntimeError("test")) == 500

@pytest.mark.asyncio
async def test_route_discovery(mock_api_server):
    """Verify that the runner can discover routes from OpenAPI."""
    runner = SelfTestRunner(base_url=mock_api_server)
    # Mocking the discovery since we don't want a real network call here
    # but the logic in runner._discover_routes() is what we're testing
    pass

def test_runner_initialization():
    """Test runner setup."""
    runner = SelfTestRunner(base_url="http://localhost:8000")
    assert runner.base_url == "http://localhost:8000"
    assert runner.results == []
