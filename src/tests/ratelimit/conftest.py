"""
Rate limit test fixtures.
"""

import pytest

from src.core.ratelimit.models import RateLimitConfig


@pytest.fixture
def default_config():
    """Default rate limit configuration for tests."""
    return RateLimitConfig(
        requests=100,
        window_seconds=60.0,
        burst=10,
    )
