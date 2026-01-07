"""
Tests for admin bypass and internal requests.
"""

from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.manager import RateLimitManager
from src.core import ratelimit


class TestAdminBypass:
    """Tests for admin user bypass."""

    def test_admin_bypasses_by_default(self, memory_storage, test_user_id):
        """Test admin users bypass rate limiting by default."""
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
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
                is_admin=True,
            )
            assert result.allowed, f"Admin request {i + 1} should be allowed"

    def test_non_admin_rate_limited(self, memory_storage, test_user_id):
        """Test non-admin users are rate limited."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=False,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=False,
        )
        assert not result.allowed


class TestInternalBypass:
    """Tests for internal request bypass."""

    def test_internal_requests_bypass(self, memory_storage, test_user_id):
        """Test internal requests bypass rate limiting."""
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
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
                is_internal=True,
            )
            assert result.allowed, f"Internal request {i + 1} should be allowed"

    def test_external_requests_rate_limited(self, memory_storage, test_user_id):
        """Test external requests are rate limited."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_internal=False,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_internal=False,
        )
        assert not result.allowed


class TestCustomBypassCheck:
    """Tests for custom bypass check function."""

    def test_custom_bypass_allows(self, memory_storage, test_user_id):
        """Test custom bypass function can allow requests."""

        def custom_bypass(user_id, is_admin, is_internal):
            return user_id == test_user_id

        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            bypass_check=custom_bypass,
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed

    def test_custom_bypass_blocks(self, memory_storage, test_user_id):
        """Test custom bypass function can block requests."""

        def custom_bypass(user_id, is_admin, is_internal):
            return False

        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            bypass_check=custom_bypass,
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert not result.allowed

    def test_custom_bypass_with_premium_users(self, memory_storage):
        """Test custom bypass for premium users."""
        premium_users = {111, 222, 333}

        def custom_bypass(user_id, is_admin, is_internal):
            if is_admin or is_internal:
                return True
            return user_id in premium_users

        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            bypass_check=custom_bypass,
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=111,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=999,
            route="GET /test",
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=999,
            route="GET /test",
        )
        assert not result.allowed

    def test_set_bypass_check_after_init(self, memory_storage, test_user_id):
        """Test setting bypass check after initialization."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        manager.reset_all()
        manager.set_bypass_check(lambda uid, admin, internal: True)
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed


class TestBypassResult:
    """Tests for bypass result properties."""

    def test_bypass_result_has_high_remaining(self, memory_storage, test_user_id):
        """Test bypass result has high remaining count."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert result.remaining > 1000

    def test_bypass_result_has_bypass_scope(self, memory_storage, test_user_id):
        """Test bypass result has bypass scope in headers."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert result.headers.scope == "bypass"


class TestModuleBypassInterface:
    """Tests for module-level bypass interface."""

    def test_module_set_bypass_check(self, setup_ratelimit, test_user_id):
        """Test module-level set_bypass_check function."""
        ratelimit.set_bypass_check(lambda uid, admin, internal: uid == test_user_id)
        for i in range(100):
            result = ratelimit.check_rate_limit(user_id=test_user_id)
            assert result.allowed
        result = ratelimit.check_rate_limit(user_id=99999)
        assert result.allowed


class TestBypassWithGlobalLimit:
    """Tests for bypass with global rate limiting."""

    def test_admin_bypasses_global_limit(self, memory_storage, test_user_id):
        """Test admin bypasses global rate limit."""
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
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                is_admin=True,
            )
            assert result.allowed

    def test_internal_bypasses_global_limit(self, memory_storage, test_user_id):
        """Test internal requests bypass global rate limit."""
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
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                is_internal=True,
            )
            assert result.allowed
