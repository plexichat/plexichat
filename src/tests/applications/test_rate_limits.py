"""
Tests for application rate limiting.
"""

import pytest


@pytest.mark.applications
@pytest.mark.integration
class TestRateLimiting:
    """Tests for rate limiting."""

    def test_check_rate_limit_allowed(self, modules, test_application):
        """Test that rate limit check passes initially."""
        app, owner = test_application

        result = modules.applications.check_rate_limit(app.id)

        assert result is True

    def test_rate_limit_exceeded(self, modules, test_application):
        """Test that rate limit is enforced."""
        app, owner = test_application

        for _ in range(100):
            try:
                modules.applications.check_rate_limit(app.id)
            except modules.applications.RateLimitError:
                return

        pytest.fail("Rate limit was not enforced")

    def test_rate_limit_error_has_retry_after(self, modules, test_application):
        """Test that rate limit error includes retry_after."""
        app, owner = test_application

        try:
            for _ in range(100):
                modules.applications.check_rate_limit(app.id)
        except modules.applications.RateLimitError as e:
            assert hasattr(e, "retry_after")
            assert e.retry_after > 0
            return

        pytest.fail("Rate limit was not enforced")
