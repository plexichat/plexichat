"""Tests for application rate limiting."""

import pytest

from src.core.applications.exceptions import RateLimitError


@pytest.mark.applications
class TestRateLimits:
    """Tests for application rate limit enforcement (shared token bucket)."""

    def _shared_storage(self):
        """Ensure the shared rate-limit module is initialised and return its storage."""
        import src.core.ratelimit as ratelimit
        from src.core.ratelimit.storage import MemoryStorage

        if not ratelimit.is_setup():
            ratelimit.setup(storage_backend=MemoryStorage(), enable_global_limit=False)
        manager = ratelimit.get_manager()
        return getattr(manager, "_storage", None)

    def _seed_bucket(self, app_id, state):
        """Seed the shared bucket state for ``app_id`` (simulates prior usage)."""
        storage = self._shared_storage()
        assert storage is not None
        storage.update_bucket(
            f"applications:rate:{int(app_id)}", lambda s: s.update(state), ttl=86400
        )

    def test_check_rate_limit_allowed(self, app_manager, test_user):
        """Test that normal requests are allowed."""
        app = app_manager.create_application(owner_id=test_user.id, name="Rate App")
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_uses_shared_storage(self, app_manager, test_user):
        """Test that the token bucket lives in the shared storage, not a per-instance dict."""
        app = app_manager.create_application(owner_id=test_user.id, name="Bucket App")
        app_manager.check_rate_limit(app.id)
        storage = self._shared_storage()
        assert storage is not None
        bucket = storage.get_bucket(f"applications:rate:{int(app.id)}")
        assert bucket is not None
        assert "tokens" in bucket
        assert "last_refill" in bucket
        assert "max_tokens" in bucket
        assert "refill_rate" in bucket

    def test_rate_limit_burst_capacity(self, app_manager, test_user):
        """Test that burst_limit requests pass and the next is denied."""
        app = app_manager.create_application(owner_id=test_user.id, name="Burst App")
        for _ in range(10):  # default burst_limit = 10
            assert app_manager.check_rate_limit(app.id) is True
        with pytest.raises(RateLimitError):
            app_manager.check_rate_limit(app.id)

    def test_rate_limit_shared_across_instances(self, db, app_manager, test_user):
        """Test that a second manager observes the same bucket (multi-worker case).

        The legacy implementation kept ``self._rate_limits`` per instance, so
        a second process/worker had an independent budget. The bucket must be
        shared: exhausting it through one manager denies the other.
        """
        from src.core.applications.manager import ApplicationManager

        other = ApplicationManager(db)
        app = app_manager.create_application(owner_id=test_user.id, name="Share App")
        for _ in range(10):
            assert app_manager.check_rate_limit(app.id) is True
        with pytest.raises(RateLimitError):
            other.check_rate_limit(app.id)

    def test_rate_limit_exceeded(self, app_manager, test_user):
        """Test that an exhausted bucket raises a RateLimitError."""
        app = app_manager.create_application(owner_id=test_user.id, name="Limit App")
        self._seed_bucket(
            app.id,
            {
                "tokens": 0.0,
                "last_refill": app_manager._get_timestamp(),
                "max_tokens": 10.0,
                "refill_rate": 50 / 60.0,
            },
        )
        with pytest.raises(RateLimitError):
            app_manager.check_rate_limit(app.id)

    def test_rate_limit_refills_over_time(self, app_manager, test_user):
        """Test that tokens refill over time (an old exhausted bucket allows again)."""
        app = app_manager.create_application(owner_id=test_user.id, name="Refill App")
        self._seed_bucket(
            app.id,
            {
                "tokens": 0.0,
                "last_refill": app_manager._get_timestamp() - 60000,
                "max_tokens": 10.0,
                "refill_rate": 50 / 60.0,
            },
        )
        result = app_manager.check_rate_limit(app.id)
        assert result is True

    def test_rate_limit_error_has_retry_after(self, app_manager, test_user):
        """Test that RateLimitError has retry_after field."""
        app = app_manager.create_application(owner_id=test_user.id, name="Retry App")
        self._seed_bucket(
            app.id,
            {
                "tokens": 0.0,
                "last_refill": app_manager._get_timestamp(),
                "max_tokens": 10.0,
                "refill_rate": 50 / 60.0,
            },
        )
        with pytest.raises(RateLimitError) as exc_info:
            app_manager.check_rate_limit(app.id)
        assert exc_info.value.retry_after > 0
