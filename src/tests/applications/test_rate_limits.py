"""Tests for application rate limiting."""

import pytest

from src.core.applications.exceptions import RateLimitError


@pytest.mark.applications
class TestRateLimits:
    """Tests for application rate limit enforcement with token bucket."""

    def test_check_rate_limit_allowed(self, app_manager, test_user):
        """Test that normal requests are allowed."""
        app = app_manager.create_application(owner_id=test_user.id, name="Rate App")
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_uses_token_bucket(self, app_manager, test_user):
        """Test that rate limiting uses a token bucket with burst capacity."""
        app = app_manager.create_application(owner_id=test_user.id, name="Bucket App")
        app_manager.check_rate_limit(app.id)
        bucket = app_manager._rate_limits.get(app.id)
        assert bucket is not None
        assert "tokens" in bucket
        assert "last_refill" in bucket
        assert "max_tokens" in bucket
        assert "refill_rate" in bucket

    def test_rate_limit_burst_capacity(self, app_manager, test_user):
        """Test that burst_limit is respected as max tokens."""
        app = app_manager.create_application(owner_id=test_user.id, name="Burst App")
        app_manager.check_rate_limit(app.id)
        bucket = app_manager._rate_limits.get(app.id)
        assert bucket["max_tokens"] == 10.0

    def test_rate_limit_depletes_tokens(self, app_manager, test_user):
        """Test that tokens are consumed on each request."""
        app = app_manager.create_application(owner_id=test_user.id, name="Deplete App")
        app_manager.check_rate_limit(app.id)
        tokens_before = app_manager._rate_limits[app.id]["tokens"]
        app_manager.check_rate_limit(app.id)
        tokens_after = app_manager._rate_limits[app.id]["tokens"]
        assert tokens_after < tokens_before

    def test_rate_limit_exceeded(self, app_manager, test_user):
        """Test that exceeding rate limit raises error."""
        app = app_manager.create_application(owner_id=test_user.id, name="Limit App")
        app_manager._rate_limits[app.id] = {
            "tokens": 0.0,
            "last_refill": app_manager._get_timestamp(),
            "max_tokens": 10.0,
            "refill_rate": 50 / 60.0,
        }
        with pytest.raises(RateLimitError):
            app_manager.check_rate_limit(app.id)

    def test_rate_limit_refills_over_time(self, app_manager, test_user):
        """Test that tokens refill over time."""
        app = app_manager.create_application(owner_id=test_user.id, name="Refill App")
        app_manager._rate_limits[app.id] = {
            "tokens": 0.0,
            "last_refill": app_manager._get_timestamp() - 60000,
            "max_tokens": 10.0,
            "refill_rate": 50 / 60.0,
        }
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_error_has_retry_after(self, app_manager, test_user):
        """Test that RateLimitError has retry_after field."""
        app = app_manager.create_application(owner_id=test_user.id, name="Retry App")
        app_manager._rate_limits[app.id] = {
            "tokens": 0.0,
            "last_refill": app_manager._get_timestamp(),
            "max_tokens": 10.0,
            "refill_rate": 50 / 60.0,
        }
        with pytest.raises(RateLimitError) as exc_info:
            app_manager.check_rate_limit(app.id)
        assert exc_info.value.retry_after > 0
