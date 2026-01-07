"""
Shared fixtures for rate limit tests.
"""

import pytest

import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.core.ratelimit.models import (  # noqa: E402
    RateLimitConfig,
    RateLimitAlgorithm,
)
from src.core.ratelimit.storage import MemoryStorage  # noqa: E402
from src.core.ratelimit.manager import RateLimitManager  # noqa: E402
from src.core import ratelimit  # noqa: E402


@pytest.fixture
def memory_storage():
    """Create a fresh memory storage instance."""
    return MemoryStorage(cleanup_interval=1.0, max_buckets=1000)


@pytest.fixture
def default_config():
    """Create a default rate limit config."""
    return RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=5,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    )


@pytest.fixture
def token_bucket_config():
    """Create a token bucket config."""
    return RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=5,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    )


@pytest.fixture
def fixed_window_config():
    """Create a fixed window config."""
    return RateLimitConfig(
        requests=5,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )


@pytest.fixture
def leaky_bucket_config():
    """Create a leaky bucket config."""
    return RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=5,
        algorithm=RateLimitAlgorithm.LEAKY_BUCKET,
    )


@pytest.fixture
def hourly_config():
    """Create a config with hourly limit."""
    return RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=0,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        hourly_limit=50,
    )


@pytest.fixture
def daily_config():
    """Create a config with daily limit."""
    return RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=0,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        daily_limit=100,
    )


@pytest.fixture
def rate_limit_manager(memory_storage):
    """Create a rate limit manager with memory storage."""
    return RateLimitManager(
        storage_backend=memory_storage,
        bot_multiplier=1.5,
        webhook_multiplier=1.0,
        enable_global_limit=True,
    )


@pytest.fixture
def setup_ratelimit(memory_storage):
    """Setup the ratelimit module for testing."""
    ratelimit._manager = None
    ratelimit._setup_complete = False
    ratelimit.setup(
        storage_backend=memory_storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )
    yield
    ratelimit._manager = None
    ratelimit._setup_complete = False


@pytest.fixture
def mock_request():
    """Create a mock request object."""

    class MockUser:
        def __init__(self, user_id=12345, token_type="user", permissions=None):
            self.user_id = user_id
            self.token_type = token_type
            self.permissions = permissions or {}

    class MockState:
        def __init__(self):
            self.user: MockUser | None = None

    class MockClient:
        def __init__(self):
            self.host = "127.0.0.1"

    class MockURL:
        def __init__(self, path="/api/v1/test"):
            self.path = path

    class MockHeaders:
        def __init__(self):
            self._headers = {}

        def get(self, key, default=None):
            return self._headers.get(key, default)

        def __setitem__(self, key, value):
            self._headers[key] = value

    class MockRequest:
        def __init__(self, method="GET", path="/api/v1/test"):
            self.method = method
            self.url = MockURL(path)
            self.state = MockState()
            self.client = MockClient()
            self.headers = MockHeaders()

        def set_user(self, user_id=12345, token_type="user", permissions=None):
            self.state.user = MockUser(user_id, token_type, permissions)

    return MockRequest


@pytest.fixture
def test_user_id():
    """Return a test user ID."""
    return 12345


@pytest.fixture
def test_channel_id():
    """Return a test channel ID."""
    return 67890


@pytest.fixture
def test_webhook_id():
    """Return a test webhook ID."""
    return 11111
