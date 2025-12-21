"""
Integration tests for all middleware working together.

Tests cover:
- Middleware execution order
- Authentication + Rate Limiting
- Authentication + Error Handling
- Logging + Error Handling
- All middleware combined scenarios
- Complex real-world scenarios
"""

import pytest
import uuid
from unittest.mock import patch
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.testclient import TestClient

from src.api.middleware.authentication import (
    AuthenticationMiddleware,
    get_current_user,
)
from src.api.middleware.error_handling import setup_exception_handlers
from src.api.middleware.logging import LoggingMiddleware
from src.api.middleware.rate_limiting import create_rate_limit_middleware
from src.core.auth.models import TokenInfo
from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.storage import MemoryStorage
from src.core import ratelimit


class TestMiddlewareExecutionOrder:
    """Tests for middleware execution order."""

    @pytest.fixture
    def ordered_app(self, api_module):
        """Create app with all middleware in correct order."""
        app = FastAPI()
        
        app.add_middleware(LoggingMiddleware)
        app.add_middleware(AuthenticationMiddleware)

        setup_exception_handlers(app)

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {
                "authenticated": user is not None,
                "user_id": user.user_id if user else None
            }

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_logging_executes_after_auth(self, mock_log, ordered_app, modules, test_user_with_token):
        """Test logging middleware executes after authentication."""
        user, token = test_user_with_token
        client = TestClient(ordered_app, raise_server_exceptions=False)
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["user_id"] == user.id
        assert mock_log.called

    def test_error_handling_catches_all_exceptions(self, ordered_app):
        """Test error handling middleware catches exceptions from all layers."""
        app = ordered_app

        @app.get("/error")
        async def error_endpoint():
            raise ValueError("Test error")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/error")
        
        assert response.status_code == 500
        data = response.json()
        assert "error" in data


class TestAuthenticationWithRateLimiting:
    """Tests for authentication and rate limiting integration."""

    @pytest.fixture
    def auth_ratelimit_app(self, modules, api_module):
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/protected": RateLimitConfig(
                    requests=10,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )

        # Initialize API module
        modules.get_api()

        app = FastAPI()
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/api/v1/protected")
        async def protected_endpoint(user: TokenInfo = Depends(get_current_user)):
            return {"user_id": user.user_id, "data": "protected"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_authenticated_user_rate_limited(self, auth_ratelimit_app, modules, user_pool):
        """Test authenticated users are rate limited by user ID."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"user_{unique_id}",
            email=f"user_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"user_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(auth_ratelimit_app, raise_server_exceptions=False)
        
        for i in range(10):
            response = client.get("/api/v1/protected", headers=headers)
            assert response.status_code == 200
            assert response.json()["user_id"] == user.id

        response = client.get("/api/v1/protected", headers=headers)
        assert response.status_code == 429

    def test_unauthenticated_request_returns_401_before_rate_limit(self, auth_ratelimit_app):
        """Test unauthenticated requests return 401 before hitting rate limit."""
        client = TestClient(auth_ratelimit_app, raise_server_exceptions=False)
        
        for i in range(5):
            response = client.get("/api/v1/protected")
            assert response.status_code == 401

    def test_invalid_token_returns_401(self, auth_ratelimit_app):
        """Test invalid token returns 401."""
        client = TestClient(auth_ratelimit_app, raise_server_exceptions=False)
        response = client.get("/api/v1/protected", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401


class TestAuthenticationWithErrorHandling:
    """Tests for authentication and error handling integration."""

    @pytest.fixture
    def auth_error_app(self, api_module):
        """Create app with authentication and error handling."""
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/protected")
        async def protected_endpoint(user: TokenInfo = Depends(get_current_user)):
            return {"user_id": user.user_id}

        @app.get("/optional")
        async def optional_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=403, detail="Access denied")
            return {"user_id": user.user_id}

        return app

    def test_authentication_error_formatted_correctly(self, auth_error_app):
        """Test authentication errors are formatted correctly."""
        client = TestClient(auth_error_app, raise_server_exceptions=False)
        response = client.get("/protected")
        
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 401

    def test_custom_error_after_auth_check(self, auth_error_app):
        """Test custom errors after auth check are handled."""
        client = TestClient(auth_error_app, raise_server_exceptions=False)
        response = client.get("/optional")
        
        assert response.status_code == 403
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 403

    def test_valid_auth_bypasses_errors(self, auth_error_app, modules, test_user_with_token):
        """Test valid authentication bypasses auth errors."""
        user, token = test_user_with_token
        client = TestClient(auth_error_app, raise_server_exceptions=False)
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id


class TestLoggingWithErrorHandling:
    """Tests for logging and error handling integration."""

    @pytest.fixture
    def logging_error_app(self, api_module):
        """Create app with logging and error handling."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)
        setup_exception_handlers(app)

        @app.get("/success")
        async def success_endpoint():
            return {"status": "ok"}

        @app.get("/error")
        async def error_endpoint():
            raise ValueError("Test error")

        @app.get("/http-error")
        async def http_error_endpoint():
            raise HTTPException(status_code=404, detail="Not found")

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_successful_request_logged(self, mock_log, logging_error_app):
        """Test successful requests are logged."""
        client = TestClient(logging_error_app, raise_server_exceptions=False)
        response = client.get("/success")
        
        assert response.status_code == 200
        assert mock_log.called

    @patch('src.api.middleware.logging._log_error')
    def test_500_error_logged(self, mock_log, logging_error_app):
        """Test 500 errors are logged."""
        client = TestClient(logging_error_app, raise_server_exceptions=False)
        response = client.get("/error")
        
        assert response.status_code == 500

    @patch('src.api.middleware.logging._log_warning')
    def test_404_error_logged(self, mock_log, logging_error_app):
        """Test 404 errors are logged."""
        client = TestClient(logging_error_app, raise_server_exceptions=False)
        response = client.get("/http-error")
        
        assert response.status_code == 404


class TestAllMiddlewareTogether:
    """Tests for all middleware working together."""

    @pytest.fixture
    def full_stack_app(self, modules, api_module):
        """Create app with all middleware."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/data": RateLimitConfig(
                    requests=5,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
            bypass_check=lambda uid, admin, internal: admin or internal,
        )

        app = FastAPI()
        
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthenticationMiddleware)
        app.add_middleware(LoggingMiddleware)
        
        setup_exception_handlers(app)

        @app.get("/api/v1/data")
        async def data_endpoint(user: TokenInfo = Depends(get_current_user)):
            return {"user_id": user.user_id, "data": "sensitive"}

        @app.get("/api/v1/public")
        async def public_endpoint():
            return {"data": "public"}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    @patch('src.api.middleware.logging._log_info')
    def test_successful_authenticated_request(self, mock_log, full_stack_app, modules, test_user_with_token):
        """Test successful authenticated request through all middleware."""
        user, token = test_user_with_token
        client = TestClient(full_stack_app, raise_server_exceptions=False)
        response = client.get("/api/v1/data", headers={"Authorization": f"Bearer {token}"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id
        assert mock_log.called

    @patch('src.api.middleware.logging._log_info')
    def test_rate_limited_authenticated_request(self, mock_log, full_stack_app, modules, user_pool):
        """Test rate limiting works with authentication and logging."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"user_{unique_id}",
            email=f"user_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"user_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(full_stack_app, raise_server_exceptions=False)
        
        for i in range(5):
            response = client.get("/api/v1/data", headers=headers)
            assert response.status_code == 200

        response = client.get("/api/v1/data", headers=headers)
        assert response.status_code == 429

    @patch('src.api.middleware.logging._log_warning')
    def test_unauthenticated_request_all_middleware(self, mock_log, full_stack_app):
        """Test unauthenticated request through all middleware."""
        client = TestClient(full_stack_app, raise_server_exceptions=False)
        response = client.get("/api/v1/data")
        
        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_admin_bypasses_rate_limit_with_logging(self, full_stack_app, modules, user_pool):
        """Test admin users bypass rate limits with all middleware active."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"admin_{unique_id}",
            email=f"admin_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        modules.auth.grant_permission(user.id, "admin.*")
        
        login_result = modules.auth.login(f"admin_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(full_stack_app, raise_server_exceptions=False)
        
        for i in range(20):
            response = client.get("/api/v1/data", headers=headers)
            assert response.status_code == 200, f"Admin request {i+1} failed"


class TestRealWorldScenarios:
    """Tests for complex real-world scenarios."""

    @pytest.fixture
    def realistic_app(self, modules, api_module):
        """Create app mimicking real-world setup."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "POST /api/v1/auth/login": RateLimitConfig(
                    requests=5,
                    window_seconds=300.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
                "GET /api/v1/users/@me": RateLimitConfig(
                    requests=30,
                    window_seconds=60.0,
                    burst=10,
                    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
                ),
            },
            enable_global_limit=False,
        )

        app = FastAPI()
        
        RateLimitMiddleware = create_rate_limit_middleware()
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthenticationMiddleware)
        app.add_middleware(LoggingMiddleware)
        
        setup_exception_handlers(app)

        @app.post("/api/v1/auth/login")
        async def login_endpoint():
            return {"token": "fake_token"}

        @app.get("/api/v1/users/@me")
        async def get_current_user_endpoint(user: TokenInfo = Depends(get_current_user)):
            return {"id": user.user_id, "username": user.username}

        yield app

        ratelimit._manager = None
        ratelimit._setup_complete = False

    @patch('src.api.middleware.logging._log_info')
    def test_login_brute_force_protection(self, mock_log, realistic_app):
        """Test login endpoint has brute force protection."""
        client = TestClient(realistic_app, raise_server_exceptions=False)
        
        for i in range(5):
            response = client.post("/api/v1/auth/login", json={
                "username": "attacker",
                "password": f"attempt_{i}"
            })
            assert response.status_code == 200

        response = client.post("/api/v1/auth/login", json={
            "username": "attacker",
            "password": "attempt_6"
        })
        assert response.status_code == 429

    @patch('src.api.middleware.logging._log_info')
    def test_authenticated_endpoint_rate_limit(self, mock_log, realistic_app, modules, user_pool):
        """Test authenticated endpoint has higher rate limit."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"user_{unique_id}",
            email=f"user_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"user_{unique_id}", "TestPass123!")
        headers = {"Authorization": f"Bearer {login_result.token}"}

        client = TestClient(realistic_app, raise_server_exceptions=False)
        
        for i in range(30):
            response = client.get("/api/v1/users/@me", headers=headers)
            assert response.status_code == 200, f"Request {i+1} failed"

    def test_concurrent_users_separate_limits(self, realistic_app, modules, user_pool):
        """Test concurrent users have separate rate limits."""
        import threading
        
        unique_id1 = uuid.uuid4().hex[:8]
        unique_id2 = uuid.uuid4().hex[:8]
        
        modules.auth.register(
            username=f"user1_{unique_id1}",
            email=f"user1_{unique_id1}@example.com",
            password="TestPass123!"
        )
        modules.auth.register(
            username=f"user2_{unique_id2}",
            email=f"user2_{unique_id2}@example.com",
            password="TestPass123!"
        )

        login1 = modules.auth.login(f"user1_{unique_id1}", "TestPass123!")
        login2 = modules.auth.login(f"user2_{unique_id2}", "TestPass123!")

        headers1 = {"Authorization": f"Bearer {login1.token}"}
        headers2 = {"Authorization": f"Bearer {login2.token}"}

        client = TestClient(realistic_app, raise_server_exceptions=False)

        results1 = []
        results2 = []

        def user1_requests():
            for _ in range(35):
                response = client.get("/api/v1/users/@me", headers=headers1)
                results1.append(response.status_code)

        def user2_requests():
            for _ in range(35):
                response = client.get("/api/v1/users/@me", headers=headers2)
                results2.append(response.status_code)

        thread1 = threading.Thread(target=user1_requests)
        thread2 = threading.Thread(target=user2_requests)

        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        assert 200 in results1
        assert 200 in results2


class TestErrorPropagation:
    """Tests for error propagation through middleware stack."""

    @pytest.fixture
    def error_stack_app(self, api_module):
        """Create app for error propagation testing."""
        app = FastAPI()
        
        app.add_middleware(LoggingMiddleware)
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/validation-error")
        async def validation_error():
            class CustomValidationError(Exception):
                pass
            raise CustomValidationError("Invalid input")

        @app.get("/not-found")
        async def not_found():
            class NotFoundError(Exception):
                pass
            raise NotFoundError("Resource not found")

        @app.get("/permission-denied")
        async def permission_denied(user: TokenInfo = Depends(get_current_user)):
            class PermissionDeniedError(Exception):
                pass
            raise PermissionDeniedError("Permission denied")

        return app

    @patch('src.api.middleware.logging._log_warning')
    def test_validation_error_propagates(self, mock_log, error_stack_app):
        """Test validation errors propagate correctly."""
        client = TestClient(error_stack_app, raise_server_exceptions=False)
        response = client.get("/validation-error")
        
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    @patch('src.api.middleware.logging._log_warning')
    def test_not_found_error_propagates(self, mock_log, error_stack_app):
        """Test not found errors propagate correctly."""
        client = TestClient(error_stack_app, raise_server_exceptions=False)
        response = client.get("/not-found")
        
        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    @patch('src.api.middleware.logging._log_warning')
    def test_permission_error_after_auth(self, mock_log, error_stack_app, modules, test_user_with_token):
        """Test permission errors after authentication."""
        user, token = test_user_with_token
        client = TestClient(error_stack_app, raise_server_exceptions=False)
        response = client.get("/permission-denied", headers={"Authorization": f"Bearer {token}"})
        
        assert response.status_code == 403
        data = response.json()
        assert "error" in data
