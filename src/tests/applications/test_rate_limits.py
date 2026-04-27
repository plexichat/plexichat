"""Tests for application rate limiting."""

import pytest

from src.core.applications.exceptions import RateLimitError


@pytest.mark.applications
class TestRateLimits:
    """Tests for application rate limit enforcement."""

    def test_check_rate_limit_allowed(self, app_manager, test_user):
        """Test that normal requests are allowed."""
        app = app_manager.create_application(owner_id=test_user.id, name="Rate App")
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_tracks_requests(self, app_manager, test_user):
        """Test that rate limit counter increments."""
        app = app_manager.create_application(owner_id=test_user.id, name="Track App")
        # First request should pass
        app_manager.check_rate_limit(app.id)
        # Counter should be 1 now
        rate_info = app_manager._rate_limits.get(app.id)
        assert rate_info is not None
        assert rate_info["count"] >= 1

    def test_rate_limit_exceeded(self, app_manager, test_user):
        """Test that exceeding rate limit raises error."""
        app = app_manager.create_application(owner_id=test_user.id, name="Limit App")
        # Simulate max requests
        app_manager._rate_limits[app.id] = {
            "count": 100,
            "reset_at": app_manager._get_timestamp() + 60000,
        }
        with pytest.raises(RateLimitError):
            app_manager.check_rate_limit(app.id)

    def test_rate_limit_resets_after_window(self, app_manager, test_user):
        """Test that rate limit resets after time window."""
        app = app_manager.create_application(owner_id=test_user.id, name="Reset App")
        # Set expired window
        app_manager._rate_limits[app.id] = {
            "count": 100,
            "reset_at": app_manager._get_timestamp() - 1,  # Already expired
        }
        # Should reset and allow
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_error_has_retry_after(self, app_manager, test_user):
        """Test that RateLimitError has retry_after field."""
        app = app_manager.create_application(owner_id=test_user.id, name="Retry App")
        app_manager._rate_limits[app.id] = {
            "count": 100,
            "reset_at": app_manager._get_timestamp() + 60000,
        }
        with pytest.raises(RateLimitError) as exc_info:
            app_manager.check_rate_limit(app.id)
        assert exc_info.value.retry_after > 0
