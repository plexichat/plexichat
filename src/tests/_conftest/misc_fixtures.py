"""Miscellaneous fixtures: email, mocker, ratelimit, ids."""

import pytest
from unittest.mock import Mock


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def email_sender():
    """Mock email sender."""
    mock = Mock()
    mock.send = Mock(return_value=True)
    return mock


@pytest.fixture
def mocker():
    """Mocker fixture for patching."""
    from unittest.mock import MagicMock, patch

    class Mocker:
        def __init__(self):
            self._patches = []
            self.MagicMock = MagicMock

        def patch(self, *args, **kwargs):
            p = patch(*args, **kwargs)
            started = p.start()
            self._patches.append(p)
            return started

        def stopall(self):
            while self._patches:
                p = self._patches.pop()
                try:
                    p.stop()
                except Exception:
                    pass

    m = Mocker()
    try:
        yield m
    finally:
        m.stopall()


# =============================================================================
# Rate Limiting Fixtures
# =============================================================================


@pytest.fixture
def memory_storage():
    """MemoryStorage fixture for rate limiting tests."""
    from src.core.ratelimit.storage import MemoryStorage

    return MemoryStorage(cleanup_interval=1.0, max_buckets=1000)


@pytest.fixture
def rate_limit_manager(memory_storage):
    """RateLimitManager fixture."""
    from src.core.ratelimit.manager import RateLimitManager

    return RateLimitManager(memory_storage)


@pytest.fixture
def setup_ratelimit(memory_storage):
    """Setup ratelimit module for module-level interface tests."""
    from src.core import ratelimit

    ratelimit.setup(
        storage_backend=memory_storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )
    return ratelimit


@pytest.fixture
def test_user_id():
    """Test user ID fixture."""
    return 12345


@pytest.fixture
def test_channel_id():
    """Test channel ID fixture."""
    return 67890


@pytest.fixture
def test_webhook_id():
    """Test webhook ID fixture."""
    return 54321
