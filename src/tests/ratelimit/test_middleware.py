"""
Tests for FastAPI middleware integration.
"""

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.core import ratelimit
from src.core.ratelimit import RateLimitMiddleware
from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.storage import MemoryStorage
from src.core.ratelimit.middleware import extract_route_info


class TestRouteExtraction:
    """Tests for route pattern extraction."""

    def test_extract_login_route(self):
        """Test extracting login route."""
        route, resource_id, webhook_id = extract_route_info("/api/v1/auth/login", "POST")
        assert route == "POST /auth/login"
        assert resource_id is None
        assert webhook_id is None

    def test_extract_messages_route(self):
        """Test extracting messages route with channel ID."""
        route, resource_id, webhook_id = extract_route_info("/api/v1/channels/12345/messages", "POST")
        assert route == "POST /channels/{id}/messages"
        assert resource_id == 12345
        assert webhook_id is None

    def test_extract_reactions_route(self):
        """Test extracting reactions route."""
        route, resource_id, webhook_id = extract_route_info(
            "/api/v1/channels/12345/messages/67890/reactions/thumbsup",
            "PUT"
        )
        assert route == "PUT /channels/{id}/messages/{msg_id}/reactions/{emoji}"
        assert resource_id == 12345

    def test_extract_webhook_route(self):
        """Test extracting webhook execution route."""
        route, resource_id, webhook_id = extract_route_info(
            "/api/v1/webhooks/11111/token123",
            "POST"
        )
        assert route == "POST /webhooks/{id}/{token}"
        assert webhook_id == 11111

    def test_extract_user_me_route(self):
        """Test extracting user @me route."""
        route, resource_id, webhook_id = extract_route_info("/api/v1/users/@me", "GET")
        assert route == "GET /users/@me"

    def test_extract_user_me_patch(self):
        """Test extracting user @me PATCH route."""
        route, resource_id, webhook_id = extract_route_info("/api/v1/users/@me", "PATCH")
        assert route == "PATCH /users/@me"

    def test_extract_unknown_route(self):
        """Test extracting unknown route."""
        route, resource_id, webhook_id = extract_route_info("/api/v1/unknown/path", "GET")
        assert route == "GET /api/v1/unknown/path"


class TestMiddlewareIntegration:
    """Tests for middleware integration with FastAPI."""

    @pytest.fixture
    def app_with_ratelimit(self):
        """Create a FastAPI app with rate limiting."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=3,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        @app.get("/api/v1/health")
        async def health_endpoint():
            return {"status": "healthy"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_middleware_allows_requests(self, app_with_ratelimit):
        """Test middleware allows requests under limit."""
        client = TestClient(app_with_ratelimit)
        response = client.get("/api/v1/test")
        assert response.status_code == 200

    def test_middleware_includes_headers(self, app_with_ratelimit):
        """Test middleware includes rate limit headers."""
        client = TestClient(app_with_ratelimit)
        response = client.get("/api/v1/test")
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

    def test_middleware_blocks_over_limit(self, app_with_ratelimit):
        """Test middleware blocks requests over limit."""
        client = TestClient(app_with_ratelimit)
        for i in range(3):
            response = client.get("/api/v1/test")
            assert response.status_code == 200
        response = client.get("/api/v1/test")
        assert response.status_code == 429

    def test_middleware_429_response_body(self, app_with_ratelimit):
        """Test 429 response has correct body."""
        client = TestClient(app_with_ratelimit)
        for i in range(3):
            client.get("/api/v1/test")
        response = client.get("/api/v1/test")
        assert response.status_code == 429
        body = response.json()
        assert "message" in body
        assert "retry_after" in body
        assert "global" in body

    def test_middleware_429_includes_retry_after(self, app_with_ratelimit):
        """Test 429 response includes Retry-After header."""
        client = TestClient(app_with_ratelimit)
        for i in range(3):
            client.get("/api/v1/test")
        response = client.get("/api/v1/test")
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_middleware_excludes_health_endpoint(self, app_with_ratelimit):
        """Test middleware excludes health endpoint."""
        client = TestClient(app_with_ratelimit)
        for i in range(10):
            response = client.get("/api/v1/health")
            assert response.status_code == 200


class TestMiddlewareWithAuth:
    """Tests for middleware with authentication."""

    @pytest.fixture
    def app_with_auth(self):
        """Create app with auth simulation."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=3,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )
        app = FastAPI()

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            class MockUser:
                user_id = 12345
                token_type = "user"
                permissions = {}
            request.state.user = MockUser()
            return await call_next(request)

        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_middleware_uses_user_id(self, app_with_auth):
        """Test middleware uses user ID from request state."""
        client = TestClient(app_with_auth)
        for i in range(3):
            response = client.get("/api/v1/test")
            assert response.status_code == 200
        response = client.get("/api/v1/test")
        assert response.status_code == 429


class TestMiddlewareBypass:
    """Tests for middleware bypass functionality."""

    @pytest.fixture
    def app_with_bypass(self):
        """Create app with bypass for admins."""
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
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            class MockUser:
                user_id = 12345
                token_type = "user"
                permissions = {"admin.*": request.headers.get("X-Admin") == "true"}
            request.state.user = MockUser()
            return await call_next(request)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_admin_bypasses_rate_limit(self, app_with_bypass):
        """Test admin users bypass rate limiting."""
        client = TestClient(app_with_bypass)
        for i in range(10):
            response = client.get("/api/v1/test", headers={"X-Admin": "true"})
            assert response.status_code == 200, f"Request {i+1} failed with {response.status_code}"

    def test_internal_request_bypasses(self, app_with_bypass):
        """Test internal requests bypass rate limiting."""
        client = TestClient(app_with_bypass)
        for i in range(10):
            response = client.get("/api/v1/test", headers={"X-Internal-Request": "true"})
            assert response.status_code == 200

    def test_normal_user_rate_limited(self, app_with_bypass):
        """Test normal users are rate limited."""
        client = TestClient(app_with_bypass)
        response = client.get("/api/v1/test")
        assert response.status_code == 200
        response = client.get("/api/v1/test")
        assert response.status_code == 429


class TestMiddlewareDisabled:
    """Tests for middleware when rate limiting is disabled."""

    def test_middleware_passes_through_when_not_setup(self):
        """Test middleware passes through when not setup."""
        ratelimit._manager = None
        ratelimit._setup_complete = False
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        client = TestClient(app)
        for i in range(100):
            response = client.get("/api/v1/test")
            assert response.status_code == 200
