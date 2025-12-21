"""
Comprehensive tests for authentication middleware.

Tests cover:
- Token validation and extraction
- Security scenarios (expired, revoked, invalid tokens)
- Different token types (user, bot)
- IP address and user agent handling
- Concurrent request handling
- Error path validation
"""

import pytest
import uuid
from fastapi import FastAPI, Request, Depends
from fastapi.testclient import TestClient
from typing import Optional

from src.api.middleware.authentication import (
    AuthenticationMiddleware,
    get_current_user,
    get_optional_user,
)
from src.core.auth.models import TokenInfo


class TestAuthenticationMiddleware:
    """Tests for AuthenticationMiddleware class."""

    @pytest.fixture
    def app_with_auth_middleware(self, api_module):
        """Create test app with authentication middleware."""
        from src.api.middleware.error_handling import setup_exception_handlers
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            if user:
                return {"authenticated": True, "user_id": user.user_id}
            return {"authenticated": False}

        return app

    def test_no_authorization_header(self, app_with_auth_middleware):
        """Test request without Authorization header sets user to None."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_valid_bearer_token(self, app_with_auth_middleware, modules, test_user_with_token):
        """Test request with valid Bearer token."""
        user, token = test_user_with_token
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["user_id"] == user.id

    def test_valid_bot_token(self, app_with_auth_middleware, modules, user_pool):
        """Test request with valid Bot token scheme."""
        unique_id = uuid.uuid4().hex[:8]
        user = user_pool.get_user()
        bot = modules.auth.create_bot(
            owner_id=user.id,
            username=f"testbot_{unique_id}",
            display_name=f"Test Bot {unique_id}"
        )

        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": f"Bot {bot.token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True

    def test_invalid_token(self, app_with_auth_middleware):
        """Test request with invalid token sets user to None."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": "Bearer invalid_token_123"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_malformed_authorization_header_single_part(self, app_with_auth_middleware):
        """Test malformed Authorization header with single part."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": "InvalidFormat"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_malformed_authorization_header_three_parts(self, app_with_auth_middleware):
        """Test malformed Authorization header with three parts."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": "Bearer token extra"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_unsupported_auth_scheme(self, app_with_auth_middleware):
        """Test unsupported authentication scheme."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_revoked_token(self, app_with_auth_middleware, modules, user_pool):
        """Test request with revoked token."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"testuser_{unique_id}",
            email=f"testuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"testuser_{unique_id}", "TestPass123!")
        token = login_result.token

        modules.auth.revoke_session(user.id, login_result.session.id)

        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_empty_authorization_header(self, app_with_auth_middleware):
        """Test empty Authorization header."""
        client = TestClient(app_with_auth_middleware)
        response = client.get("/test", headers={"Authorization": ""})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_bearer_scheme_strict_case(self, app_with_auth_middleware, modules, test_user_with_token):
        """Test that Bearer scheme is case sensitive (strict)."""
        user, token = test_user_with_token
        client = TestClient(app_with_auth_middleware)
        
        # Valid case
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["authenticated"] is True

        # Invalid cases
        for scheme in ["bearer", "BEARER", "bEaReR"]:
            response = client.get("/test", headers={"Authorization": f"{scheme} {token}"})
            assert response.status_code == 200
            assert response.json()["authenticated"] is False

    def test_auth_module_not_available(self, app_with_auth_middleware):
        """Test handling when auth module is not available."""
        import src.api as api
        original_auth = api._auth
        api._auth = None

        try:
            client = TestClient(app_with_auth_middleware)
            response = client.get("/test", headers={"Authorization": "Bearer some_token"})
            assert response.status_code == 200
            data = response.json()
            assert data["authenticated"] is False
        finally:
            api._auth = original_auth

    def test_extract_token_method(self):
        """Test the internal _extract_token method."""
        middleware = AuthenticationMiddleware(None)
        
        assert middleware._extract_token("Bearer token123") == "token123"
        assert middleware._extract_token("Bot token456") == "token456"
        # Invalid cases
        assert middleware._extract_token("bearer token789") is None
        assert middleware._extract_token("BOT token000") is None
        assert middleware._extract_token("Basic dXNlcjpwYXNz") is None
        assert middleware._extract_token("Bearer") is None
        assert middleware._extract_token("Bearer token extra") is None


class TestGetCurrentUserDependency:
    """Tests for get_current_user dependency."""

    @pytest.fixture
    def app_with_current_user(self):
        """Create test app using get_current_user dependency."""
        from src.api.middleware.error_handling import setup_exception_handlers
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/protected")
        async def protected_endpoint(user: TokenInfo = Depends(get_current_user)):
            return {"user_id": user.user_id, "username": user.username}

        return app

    def test_authenticated_request(self, app_with_current_user, modules, test_user_with_token):
        """Test authenticated request to protected endpoint."""
        user, token = test_user_with_token
        client = TestClient(app_with_current_user)
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id

    def test_missing_authorization_header(self, app_with_current_user):
        """Test missing authorization header returns 401."""
        client = TestClient(app_with_current_user)
        response = client.get("/protected")
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 401
        assert "authentication required" in data["error"]["message"].lower()

    def test_invalid_token_returns_401(self, app_with_current_user):
        """Test invalid token returns 401."""
        client = TestClient(app_with_current_user)
        response = client.get("/protected", headers={"Authorization": "Bearer invalid_token"})
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 401
        assert "invalid token" in data["error"]["message"].lower()

    def test_revoked_token_returns_401(self, app_with_current_user, modules, user_pool):
        """Test revoked token returns 401 with specific message."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"testuser_{unique_id}",
            email=f"testuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"testuser_{unique_id}", "TestPass123!")
        
        modules.auth.revoke_session(user.id, login_result.session.id)

        client = TestClient(app_with_current_user)
        response = client.get("/protected", headers={"Authorization": f"Bearer {login_result.token}"})
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 401

    def test_auth_module_not_available(self, app_with_current_user):
        """Test error when auth module is not available."""
        import src.api as api
        original_auth = api._auth
        api._auth = None

        try:
            client = TestClient(app_with_current_user)
            response = client.get("/protected", headers={"Authorization": "Bearer token"})
            assert response.status_code == 500
            data = response.json()
            assert "error" in data
            assert data["error"]["code"] == 500
        finally:
            api._auth = original_auth

    def test_uses_cached_user_from_middleware(self, app_with_current_user, modules, test_user_with_token):
        """Test that dependency uses user cached by middleware."""
        user, token = test_user_with_token
        app = app_with_current_user
        app.add_middleware(AuthenticationMiddleware)

        client = TestClient(app)
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id


class TestGetOptionalUserDependency:
    """Tests for get_optional_user dependency."""

    @pytest.fixture
    def app_with_optional_user(self):
        """Create test app with get_optional_user dependency."""
        from src.api.middleware.error_handling import setup_exception_handlers
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/optional")
        async def optional_endpoint(user: Optional[TokenInfo] = Depends(get_optional_user)):
            if user:
                return {"authenticated": True, "user_id": user.user_id}
            return {"authenticated": False}

        return app

    def test_authenticated_request(self, app_with_optional_user, modules, test_user_with_token):
        """Test authenticated request returns user info."""
        user, token = test_user_with_token
        client = TestClient(app_with_optional_user)
        response = client.get("/optional", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["user_id"] == user.id

    def test_unauthenticated_request(self, app_with_optional_user):
        """Test unauthenticated request returns None."""
        client = TestClient(app_with_optional_user)
        response = client.get("/optional")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_invalid_token_returns_none(self, app_with_optional_user):
        """Test invalid token returns None instead of error."""
        client = TestClient(app_with_optional_user)
        response = client.get("/optional", headers={"Authorization": "Bearer invalid_token"})
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False

    def test_auth_module_not_available(self, app_with_optional_user):
        """Test returns None when auth module is not available."""
        import src.api as api
        original_auth = api._auth
        api._auth = None

        try:
            client = TestClient(app_with_optional_user)
            response = client.get("/optional", headers={"Authorization": "Bearer token"})
            assert response.status_code == 200
            data = response.json()
            assert data["authenticated"] is False
        finally:
            api._auth = original_auth


class TestSecurityScenarios:
    """Security-focused tests for authentication middleware."""

    @pytest.fixture
    def security_app(self):
        """Create test app with sensitive endpoint for security tests."""
        from src.api.middleware.error_handling import setup_exception_handlers
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)
        setup_exception_handlers(app)

        @app.get("/sensitive")
        async def sensitive_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            if not user:
                return {"error": "Unauthorized"}
            return {"data": "sensitive_information"}

        return app

    def test_token_reuse_after_logout(self, security_app, modules, user_pool):
        """Test that token cannot be reused after logout."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"testuser_{unique_id}",
            email=f"testuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"testuser_{unique_id}", "TestPass123!")
        token = login_result.token

        client = TestClient(security_app)
        response = client.get("/sensitive", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

        # Logout the session
        modules.auth.logout(login_result.token)

        response = client.get("/sensitive", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data

    def test_sql_injection_in_token(self, security_app):
        """Test SQL injection attempts in token."""
        client = TestClient(security_app)
        malicious_tokens = [
            "' OR '1'='1",
            "1'; DROP TABLE users--",
            "admin'--",
            "' UNION SELECT * FROM users--"
        ]

        for token in malicious_tokens:
            response = client.get("/sensitive", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            data = response.json()
            assert "error" in data

    def test_xss_in_token(self, security_app):
        """Test XSS attempts in token."""
        client = TestClient(security_app)
        xss_tokens = [
            "<script>alert('xss')</script>",
            "javascript:alert('xss')",
            "<img src=x onerror=alert('xss')>"
        ]

        for token in xss_tokens:
            response = client.get("/sensitive", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            data = response.json()
            assert "error" in data

    def test_extremely_long_token(self, security_app):
        """Test handling of extremely long tokens."""
        client = TestClient(security_app)
        long_token = "a" * 100000
        response = client.get("/sensitive", headers={"Authorization": f"Bearer {long_token}"})
        assert response.status_code == 200

    def test_special_characters_in_token(self, security_app):
        """Test special characters in token."""
        client = TestClient(security_app)
        special_tokens = [
            "token\x00null",
            "token\nline",
            "token\ttab",
            "token\rcarriage"
        ]

        for token in special_tokens:
            response = client.get("/sensitive", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200


class TestConcurrency:
    """Test concurrent authentication requests."""

    @pytest.fixture
    def concurrent_app(self):
        """Create app for concurrency testing."""
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/concurrent")
        async def concurrent_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            if user:
                return {"user_id": user.user_id}
            return {"user_id": None}

        return app

    @pytest.mark.asyncio
    async def test_concurrent_valid_tokens(self, concurrent_app, modules, user_pool):
        """Test multiple concurrent requests with valid tokens."""
        import asyncio
        from httpx import AsyncClient, ASGITransport

        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"testuser_{unique_id}",
            email=f"testuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"testuser_{unique_id}", "TestPass123!")
        token = login_result.token

        async with AsyncClient(transport=ASGITransport(app=concurrent_app), base_url="http://test") as client:
            tasks = [
                client.get("/concurrent", headers={"Authorization": f"Bearer {token}"})
                for _ in range(50)
            ]
            responses = await asyncio.gather(*tasks)

        for response in responses:
            assert response.status_code == 200
            data = response.json()
            assert data["user_id"] == user.id

    @pytest.mark.asyncio
    async def test_concurrent_mixed_tokens(self, concurrent_app, modules, user_pool):
        """Test concurrent requests with mix of valid and invalid tokens."""
        import asyncio
        from httpx import AsyncClient, ASGITransport

        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"testuser_{unique_id}",
            email=f"testuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        login_result = modules.auth.login(f"testuser_{unique_id}", "TestPass123!")
        valid_token = login_result.token

        async with AsyncClient(transport=ASGITransport(app=concurrent_app), base_url="http://test") as client:
            tasks = []
            for i in range(50):
                if i % 2 == 0:
                    tasks.append(client.get("/concurrent", headers={"Authorization": f"Bearer {valid_token}"}))
                else:
                    tasks.append(client.get("/concurrent", headers={"Authorization": f"Bearer invalid_{i}"}))
            
            responses = await asyncio.gather(*tasks)

        valid_count = 0
        invalid_count = 0
        for i, response in enumerate(responses):
            assert response.status_code == 200
            data = response.json()
            if i % 2 == 0:
                assert data["user_id"] == user.id
                valid_count += 1
            else:
                assert data["user_id"] is None
                invalid_count += 1

        assert valid_count == 25
        assert invalid_count == 25


class TestIPAddressAndUserAgent:
    """Test IP address and user agent handling."""

    @pytest.fixture
    def tracking_app(self):
        """Create app that exposes request tracking."""
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/track")
        async def track_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {
                "authenticated": user is not None,
                "ip": request.client.host if request.client else None
            }

        return app

    def test_ip_address_extraction(self, tracking_app):
        """Test IP address is properly extracted from request."""
        client = TestClient(tracking_app)
        response = client.get("/track")
        assert response.status_code == 200
        data = response.json()
        assert "ip" in data

    def test_user_agent_header(self, tracking_app, modules, test_user_with_token):
        """Test user agent is properly extracted."""
        user, token = test_user_with_token
        client = TestClient(tracking_app)
        response = client.get(
            "/track",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "TestClient/1.0"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
