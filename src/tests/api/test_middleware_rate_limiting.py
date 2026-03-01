"""
Comprehensive tests for rate limiting middleware integration.

Tests cover:
- User info extraction from requests
- Rate limit enforcement
- Bypass functionality (admin, internal, bot users)
- Header inclusion
- IP address extraction
- Security scenarios
- Integration with authentication
"""

import pytest
import uuid
from unittest.mock import Mock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware.rate_limiting import (
    get_user_info_from_request,
    create_rate_limit_middleware,
)
from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.storage import MemoryStorage
from src.core import ratelimit


class TestGetUserInfoFromRequest:
    """Tests for extracting user info from requests."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock request."""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock()
        request.client.host = "192.168.1.1"
        request.state = Mock()
        return request

    def test_extract_ip_from_client(self, mock_request):
        """Test IP address is extracted from request.client."""
        user_info = get_user_info_from_request(mock_request)
        assert user_info["ip_address"] == "192.168.1.1"

    def test_extract_ip_from_x_forwarded_for(self, mock_request):
        """Test IP address is extracted from X-Forwarded-For header."""
        mock_request.headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
        user_info = get_user_info_from_request(mock_request)
        assert user_info["ip_address"] == "10.0.0.1"

    def test_no_client_ip(self, mock_request):
        """Test when no client IP is available."""
        mock_request.client = None
        user_info = get_user_info_from_request(mock_request)
        assert user_info["ip_address"] is None

    def test_authenticated_user(self, mock_request):
        """Test extracting info from authenticated user."""
        mock_user = Mock()
        mock_user.user_id = 12345
        mock_user.token_type = "user"
        mock_user.permissions = {}
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["user_id"] == 12345
        assert user_info["is_bot"] is False
        assert user_info["is_admin"] is False

    def test_bot_user(self, mock_request):
        """Test extracting info from bot user."""
        mock_user = Mock()
        mock_user.user_id = 67890
        mock_user.token_type = "bot"
        mock_user.permissions = {}
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["user_id"] == 67890
        assert user_info["is_bot"] is True

    def test_admin_user_with_admin_wildcard(self, mock_request):
        """Test admin detection with admin.* permission."""
        mock_user = Mock()
        mock_user.user_id = 11111
        mock_user.token_type = "user"
        mock_user.permissions = {"admin.*": True}
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_admin"] is True

    def test_admin_user_with_wildcard(self, mock_request):
        """Test admin detection with * permission."""
        mock_user = Mock()
        mock_user.user_id = 22222
        mock_user.token_type = "user"
        mock_user.permissions = {"*": True}
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_admin"] is True

    def test_admin_user_with_admin_permission(self, mock_request):
        """Test admin detection with admin permission."""
        mock_user = Mock()
        mock_user.user_id = 33333
        mock_user.token_type = "user"
        mock_user.permissions = {"admin": True}
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_admin"] is True

    def test_internal_request_header(self, mock_request):
        """Test internal request detection via X-Internal-Request header."""
        mock_request.headers = {"X-Internal-Request": "true"}
        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_internal"] is True

    def test_internal_request_case_insensitive(self, mock_request):
        """Test X-Internal-Request header is case insensitive."""
        mock_request.headers = {"X-Internal-Request": "TRUE"}
        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_internal"] is True

    def test_rate_limit_bypass_header(self, mock_request):
        """Test rate limit bypass via X-RateLimit-Bypass header."""
        mock_request.headers = {"X-RateLimit-Bypass": "secret"}
        user_info = get_user_info_from_request(mock_request)
        assert user_info["is_internal"] is True

    def test_no_user_state(self, mock_request):
        """Test when no user is in request state."""
        mock_request.state = Mock(spec=[])
        user_info = get_user_info_from_request(mock_request)
        assert user_info["user_id"] is None
        assert user_info["is_bot"] is False
        assert user_info["is_admin"] is False

    def test_user_without_permissions_attribute(self, mock_request):
        """Test user without permissions attribute."""
        mock_user = Mock()
        mock_user.user_id = 44444
        mock_user.token_type = "user"
        delattr(mock_user, "permissions")
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["user_id"] == 44444
        assert user_info["is_admin"] is False

    def test_permissions_not_dict(self, mock_request):
        """Test when permissions is not a dict."""
        mock_user = Mock()
        mock_user.user_id = 55555
        mock_user.token_type = "user"
        mock_user.permissions = None
        mock_request.state.user = mock_user

        user_info = get_user_info_from_request(mock_request)
        assert user_info["user_id"] == 55555
        assert user_info["is_admin"] is False


class TestCreateRateLimitMiddleware:
    """Tests for rate limit middleware creation."""

    def test_creates_middleware_class(self):
        """Test creates a middleware class."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(storage_backend=storage)

        try:
            middleware_class = create_rate_limit_middleware()
            assert middleware_class is not None
            assert callable(middleware_class)
        finally:
            ratelimit._manager = None
            ratelimit._setup_complete = False

    def test_middleware_excludes_default_paths(self):
        """Test middleware excludes default paths."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(storage_backend=storage)

        try:
            middleware_class = create_rate_limit_middleware()
            app = FastAPI()
            middleware = middleware_class(app)

            default_excludes = ["/", "/health", "/docs", "/redoc", "/openapi.json"]
            for path in default_excludes:
                assert path in middleware._exclude_paths
        finally:
            ratelimit._manager = None
            ratelimit._setup_complete = False

    def test_middleware_with_custom_excludes(self):
        """Test middleware with custom exclude paths."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(storage_backend=storage)

        try:
            middleware_class = create_rate_limit_middleware(exclude_paths=["/custom"])
            app = FastAPI()
            middleware = middleware_class(app)

            assert "/custom" in middleware._exclude_paths
            assert "/health" in middleware._exclude_paths
        finally:
            ratelimit._manager = None
            ratelimit._setup_complete = False


class TestRateLimitEnforcement:
    """Tests for rate limit enforcement."""

    @pytest.fixture
    def rate_limited_app(self):
        """Create app with rate limiting."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/limited": RateLimitConfig(
                    requests=3,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/limited")
        async def limited_endpoint():
            return {"status": "ok"}

        @app.get("/api/v1/unlimited")
        async def unlimited_endpoint():
            return {"status": "ok"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_allows_requests_under_limit(self, rate_limited_app):
        """Test allows requests under the limit."""
        client = TestClient(rate_limited_app)
        for i in range(3):
            response = client.get("/api/v1/limited")
            assert response.status_code == 200, f"Request {i + 1} failed"

    def test_blocks_requests_over_limit(self, rate_limited_app):
        """Test blocks requests over the limit."""
        client = TestClient(rate_limited_app)
        for i in range(3):
            response = client.get("/api/v1/limited")
            assert response.status_code == 200

        response = client.get("/api/v1/limited")
        assert response.status_code == 429

    def test_429_response_format(self, rate_limited_app):
        """Test 429 response has correct format."""
        client = TestClient(rate_limited_app)
        for i in range(3):
            client.get("/api/v1/limited")

        response = client.get("/api/v1/limited")
        assert response.status_code == 429
        data = response.json()
        assert "message" in data
        assert "retry_after" in data

    def test_includes_rate_limit_headers(self, rate_limited_app):
        """Test includes rate limit headers in response."""
        client = TestClient(rate_limited_app)
        response = client.get("/api/v1/limited")
        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

    def test_unlimited_endpoint_not_rate_limited(self, rate_limited_app):
        """Test unlimited endpoints are not rate limited."""
        client = TestClient(rate_limited_app)
        for i in range(10):
            response = client.get("/api/v1/unlimited")
            assert response.status_code == 200


class TestBypassFunctionality:
    """Tests for rate limit bypass."""

    @pytest.fixture
    def bypass_app(self, modules):
        """Create app with bypass functionality."""
        from src.api.middleware.authentication import AuthenticationMiddleware

        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=1,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
            bypass_check=lambda uid, admin, internal: admin or internal,
        )

        # Initialize API module to ensure auth module is available to middleware
        modules.get_api()

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_admin_bypasses_rate_limit(self, bypass_app, modules, user_pool):
        """Test admin users bypass rate limiting."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"teststaff_{unique_id}",
            email=f"teststaff_{unique_id}@example.com",
            password="TestPass123!",
        )

        modules.auth.grant_permission(user.id, "admin.*")

        login_result = modules.auth.login(f"teststaff_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(bypass_app)
        for i in range(10):
            response = client.get("/api/v1/test", headers=headers)
            assert response.status_code == 200, f"Admin request {i + 1} failed"

    def test_internal_request_bypasses(self, bypass_app):
        """Test internal requests bypass rate limiting."""
        client = TestClient(bypass_app)
        headers = {"X-Internal-Request": "true"}

        for i in range(10):
            response = client.get("/api/v1/test", headers=headers)
            assert response.status_code == 200, f"Internal request {i + 1} failed"

    def test_normal_user_rate_limited(self, bypass_app, modules, user_pool):
        """Test normal users are rate limited."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"user_{unique_id}",
            email=f"user_{unique_id}@example.com",
            password="TestPass123!",
        )
        login_result = modules.auth.login(f"user_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(bypass_app)
        response = client.get("/api/v1/test", headers=headers)
        assert response.status_code == 200

        response = client.get("/api/v1/test", headers=headers)
        assert response.status_code == 429

    def test_bypass_header_works(self, bypass_app):
        """Test X-RateLimit-Bypass header bypasses limits."""
        client = TestClient(bypass_app)
        headers = {"X-RateLimit-Bypass": "secret"}

        for i in range(10):
            response = client.get("/api/v1/test", headers=headers)
            assert response.status_code == 200


class TestIPBasedRateLimiting:
    """Tests for IP-based rate limiting."""

    @pytest.fixture
    def ip_limited_app(self):
        """Create app with IP-based rate limiting."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/public": RateLimitConfig(
                    requests=5,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/public")
        async def public_endpoint():
            return {"status": "ok"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_ip_based_limit_unauthenticated(self, ip_limited_app):
        """Test IP-based limiting for unauthenticated requests."""
        client = TestClient(ip_limited_app)

        for i in range(5):
            response = client.get("/api/v1/public")
            assert response.status_code == 200, f"Request {i + 1} failed"

        response = client.get("/api/v1/public")
        assert response.status_code == 429


class TestSecurityScenarios:
    """Security tests for rate limiting."""

    @pytest.fixture
    def security_app(self):
        """Create app for security testing."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "POST /auth/login": RateLimitConfig(
                    requests=3,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)

        @app.post("/api/v1/auth/login")
        async def login_endpoint():
            return {"token": "fake_token"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_brute_force_protection(self, security_app):
        """Test rate limiting protects against brute force attacks."""
        client = TestClient(security_app)

        for i in range(3):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "attacker", "password": f"attempt_{i}"},
            )
            assert response.status_code == 200

        response = client.post(
            "/api/v1/auth/login", json={"username": "attacker", "password": "attempt_4"}
        )
        assert response.status_code == 429

    def test_injection_in_headers_handled(self, security_app):
        """Test SQL injection in headers is handled safely."""
        client = TestClient(security_app)
        malicious_headers = {
            "X-Forwarded-For": "'; DROP TABLE users--",
            "X-Internal-Request": "<script>alert('xss')</script>",
        }

        response = client.post("/api/v1/auth/login", headers=malicious_headers)
        assert response.status_code in [200, 429]


class TestIntegrationWithAuthentication:
    """Tests for rate limiting integration with authentication."""

    @pytest.fixture
    def integrated_app(self):
        """Create app with both authentication and rate limiting."""
        from src.api.middleware.authentication import AuthenticationMiddleware

        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/user-data": RateLimitConfig(
                    requests=5,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/api/v1/user-data")
        async def user_data_endpoint():
            return {"data": "sensitive"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_authenticated_requests_tracked_by_user(
        self, integrated_app, modules, user_pool
    ):
        """Test authenticated requests are tracked by user ID."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"user_{unique_id}",
            email=f"user_{unique_id}@example.com",
            password="TestPass123!",
        )
        login_result = modules.auth.login(f"user_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(integrated_app)

        for i in range(5):
            response = client.get("/api/v1/user-data", headers=headers)
            assert response.status_code == 200, f"Request {i + 1} failed"

        response = client.get("/api/v1/user-data", headers=headers)
        assert response.status_code == 429

    def test_different_users_different_limits(self, integrated_app, modules, user_pool):
        """Test different users have separate rate limits."""
        unique_id1 = uuid.uuid4().hex[:8]
        unique_id2 = uuid.uuid4().hex[:8]

        modules.auth.register(
            username=f"user1_{unique_id1}",
            email=f"user1_{unique_id1}@example.com",
            password="TestPass123!",
        )
        modules.auth.register(
            username=f"user2_{unique_id2}",
            email=f"user2_{unique_id2}@example.com",
            password="TestPass123!",
        )

        login1 = modules.auth.login(f"user1_{unique_id1}", "TestPass123!")
        login2 = modules.auth.login(f"user2_{unique_id2}", "TestPass123!")

        headers1 = {"Authorization": f"Bearer {login1.token}"}
        headers2 = {"Authorization": f"Bearer {login2.token}"}

        client = TestClient(integrated_app)

        for i in range(5):
            response = client.get("/api/v1/user-data", headers=headers1)
            assert response.status_code == 200

        response = client.get("/api/v1/user-data", headers=headers1)
        assert response.status_code == 429

        response = client.get("/api/v1/user-data", headers=headers2)
        assert response.status_code == 200


class TestEdgeCases:
    """Tests for edge cases in rate limiting."""

    @pytest.fixture
    def edge_case_app(self):
        """Create app for edge case testing."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_no_rate_limit_config(self, edge_case_app):
        """Test endpoints without rate limit config are not limited."""
        client = TestClient(edge_case_app)

        # Default route limit is 30, so we make fewer than that
        for i in range(25):
            response = client.get("/api/v1/test")
            assert response.status_code == 200

    def test_excluded_path_not_limited(self):
        """Test excluded paths are not rate limited."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            enable_global_limit=False,
        )

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware(exclude_paths=["/health"])
        app.add_middleware(RateLimitMiddleware)

        @app.get("/health")
        async def health_endpoint():
            return {"status": "healthy"}

        client = TestClient(app)

        for i in range(100):
            response = client.get("/health")
            assert response.status_code == 200

        ratelimit._manager = None
        ratelimit._setup_complete = False
