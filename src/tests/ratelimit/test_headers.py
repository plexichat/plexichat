"""
Tests for rate limit header generation.
"""

import time

from src.core.ratelimit.models import (
    RateLimitConfig,
    RateLimitHeaders,
    RateLimitAlgorithm,
)
from src.core.ratelimit.manager import RateLimitManager


class TestHeaderGeneration:
    """Tests for rate limit header generation."""

    def test_headers_include_limit(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Limit."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Limit" in headers
        assert headers["X-RateLimit-Limit"] == "10"

    def test_headers_include_remaining(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Remaining."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Remaining" in headers
        assert int(headers["X-RateLimit-Remaining"]) == 9

    def test_headers_include_reset(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Reset."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Reset" in headers
        reset_time = float(headers["X-RateLimit-Reset"])
        assert reset_time > time.time()

    def test_headers_include_reset_after(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Reset-After."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Reset-After" in headers
        reset_after = float(headers["X-RateLimit-Reset-After"])
        assert reset_after > 0

    def test_headers_include_bucket(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Bucket."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Bucket" in headers
        assert len(headers["X-RateLimit-Bucket"]) > 0

    def test_headers_include_scope(self, memory_storage, test_user_id):
        """Test headers include X-RateLimit-Scope."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        headers = manager.get_headers(result)
        assert "X-RateLimit-Scope" in headers


class TestGlobalHeader:
    """Tests for global rate limit header."""

    def test_global_header_on_global_limit(self, memory_storage, test_user_id):
        """Test X-RateLimit-Global header when global limit hit."""
        global_config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            global_config=global_config,
            enable_global_limit=True,
        )
        manager.check_rate_limit(user_id=test_user_id)
        result = manager.check_rate_limit(user_id=test_user_id)
        headers = manager.get_headers(result)
        assert "X-RateLimit-Global" in headers
        assert headers["X-RateLimit-Global"] == "true"

    def test_no_global_header_on_route_limit(self, memory_storage, test_user_id):
        """Test no X-RateLimit-Global header when route limit hit."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        headers = manager.get_headers(result)
        assert "X-RateLimit-Global" not in headers


class TestRetryAfterHeader:
    """Tests for Retry-After header."""

    def test_retry_after_on_rate_limit(self, memory_storage, test_user_id):
        """Test Retry-After header when rate limited."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        headers = manager.get_headers(result)
        assert "Retry-After" in headers
        retry_after = int(headers["Retry-After"])
        assert retry_after > 0

    def test_no_retry_after_when_allowed(self, memory_storage, test_user_id):
        """Test no Retry-After header when request allowed."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        headers = manager.get_headers(result)
        assert "Retry-After" not in headers


class TestHeadersModel:
    """Tests for RateLimitHeaders model."""

    def test_headers_to_dict(self):
        """Test RateLimitHeaders.to_dict() method."""
        headers = RateLimitHeaders(
            limit=10,
            remaining=5,
            reset=time.time() + 60,
            reset_after=60.0,
            bucket="abc123",
            is_global=False,
            scope="user",
        )
        header_dict = headers.to_dict()
        assert "X-RateLimit-Limit" in header_dict
        assert "X-RateLimit-Remaining" in header_dict
        assert "X-RateLimit-Reset" in header_dict
        assert "X-RateLimit-Reset-After" in header_dict
        assert "X-RateLimit-Bucket" in header_dict
        assert "X-RateLimit-Scope" in header_dict

    def test_headers_with_retry_after(self):
        """Test headers include Retry-After when set."""
        headers = RateLimitHeaders(
            limit=10,
            remaining=0,
            reset=time.time() + 60,
            reset_after=60.0,
            bucket="abc123",
            is_global=False,
            retry_after=60.0,
            scope="user",
        )
        header_dict = headers.to_dict()
        assert "Retry-After" in header_dict

    def test_headers_with_global_flag(self):
        """Test headers include global flag when set."""
        headers = RateLimitHeaders(
            limit=10,
            remaining=0,
            reset=time.time() + 60,
            reset_after=60.0,
            bucket="abc123",
            is_global=True,
            scope="global",
        )
        header_dict = headers.to_dict()
        assert "X-RateLimit-Global" in header_dict
        assert header_dict["X-RateLimit-Global"] == "true"

    def test_remaining_never_negative(self):
        """Test remaining is never negative in headers."""
        headers = RateLimitHeaders(
            limit=10,
            remaining=-5,
            reset=time.time() + 60,
            reset_after=60.0,
            bucket="abc123",
            is_global=False,
            scope="user",
        )
        header_dict = headers.to_dict()
        assert int(header_dict["X-RateLimit-Remaining"]) == 0


class TestResponseBody:
    """Tests for rate limit response body."""

    def test_response_body_structure(self, memory_storage, test_user_id):
        """Test 429 response body structure."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        body = result.response_body
        assert "message" in body
        assert "retry_after" in body
        assert "global" in body

    def test_response_body_message(self, memory_storage, test_user_id):
        """Test response body has correct message."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        body = result.response_body
        assert "rate limit" in body["message"].lower()

    def test_response_body_global_flag(self, memory_storage, test_user_id):
        """Test response body global flag."""
        global_config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            global_config=global_config,
            enable_global_limit=True,
        )
        manager.check_rate_limit(user_id=test_user_id)
        result = manager.check_rate_limit(user_id=test_user_id)
        body = result.response_body
        assert body["global"] is True

    def test_response_body_scope(self, memory_storage, test_user_id):
        """Test response body includes scope."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        body = result.response_body
        assert "scope" in body
