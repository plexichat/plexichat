"""
Tests for authentication middleware - token validation, bearer/bot tokens, revocation propagation.

Covers:
- Bearer token validation
- Bot token validation
- Token revocation propagation
- Token expiry
- Invalid/malformed tokens
- Concurrent authentication requests
- Session management
"""

import pytest
import asyncio
import uuid
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestTokenValidation:
    """Test token validation and authentication flow."""

    async def test_valid_bearer_token(self, auth_headers):
        """Test request with valid Bearer token."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert "username" in data

    async def test_invalid_bearer_token(self):
        """Test request with invalid Bearer token."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me",
                headers={"Authorization": "Bearer invalid_token_12345"},
            )
            assert response.status_code == 401
            assert "error" in response.json()
            assert response.json()["error"]["message"].lower() in [
                "invalid token",
                "authentication required",
            ]

    async def test_malformed_authorization_header(self):
        """Test request with malformed Authorization header."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Missing Bearer prefix
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": "sometoken123"}
            )
            assert response.status_code == 401

            # Empty token
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": "Bearer "}
            )
            assert response.status_code == 401

            # No token at all
            response = await ac.get("/api/v1/users/@me")
            assert response.status_code == 401

    async def test_missing_authorization_header(self):
        """Test authenticated endpoint without Authorization header."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me")
            assert response.status_code == 401
            assert "error" in response.json()

    async def test_token_case_sensitivity(self, auth_headers):
        """Test that bearer scheme is case-insensitive."""
        app = create_app()
        token = auth_headers["Authorization"].split()[1]
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # lowercase bearer
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"bearer {token}"}
            )
            assert response.status_code == 200

            # uppercase BEARER
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"BEARER {token}"}
            )
            assert response.status_code == 200


@pytest.mark.asyncio
class TestBotTokens:
    """Test bot token authentication."""

    async def test_bot_token_authentication(self, modules):
        """Test request with Bot token scheme."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"botowner_{unique_id}",
            email=f"botowner_{unique_id}@example.com",
            password="SecurePass123!",
        )

        bot = modules.auth.create_bot(
            owner_id=user.id,
            username=f"testbot_{unique_id}",
            display_name=f"Test Bot {unique_id}",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"Bot {bot.token}"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["username"] == f"testbot_{unique_id}"

    async def test_invalid_bot_token(self):
        """Test request with invalid Bot token."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": "Bot invalid_bot_token"}
            )
            assert response.status_code == 401

    async def test_bot_token_case_insensitive(self, modules):
        """Test bot scheme is case-insensitive."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"botowner_{unique_id}",
            email=f"botowner_{unique_id}@example.com",
            password="SecurePass123!",
        )

        bot = modules.auth.create_bot(
            owner_id=user.id,
            username=f"testbot_{unique_id}",
            display_name=f"Test Bot {unique_id}",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # lowercase bot
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"bot {bot.token}"}
            )
            assert response.status_code == 200

            # uppercase BOT
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"BOT {bot.token}"}
            )
            assert response.status_code == 200


@pytest.mark.asyncio
class TestTokenRevocation:
    """Test token revocation propagation."""

    async def test_token_revocation_immediate(self, modules):
        """Test that token revocation is immediate across API requests."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"revoketest_{unique_id}",
            email=f"revoketest_{unique_id}@example.com",
            password="TestPass123!",
        )
        login_result = modules.auth.login(f"revoketest_{unique_id}", "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # First request should succeed
            resp1 = await ac.get("/api/v1/users/@me", headers=headers)
            assert resp1.status_code == 200

            # Revoke session
            modules.auth.revoke_session(user.id, login_result.session.id)

            # Second request should fail immediately
            resp2 = await ac.get("/api/v1/users/@me", headers=headers)
            assert resp2.status_code == 401
            assert "revoked" in resp2.json()["error"]["message"].lower()

    async def test_multiple_session_revocation(self, modules):
        """Test revoking one session doesn't affect other sessions."""
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"multisession_{unique_id}",
            email=f"multisession_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create two sessions
        login1 = modules.auth.login(f"multisession_{unique_id}", "TestPass123!")
        login2 = modules.auth.login(f"multisession_{unique_id}", "TestPass123!")

        headers1 = {"Authorization": f"Bearer {login1.token}"}
        headers2 = {"Authorization": f"Bearer {login2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Both sessions work
            resp1 = await ac.get("/api/v1/users/@me", headers=headers1)
            resp2 = await ac.get("/api/v1/users/@me", headers=headers2)
            assert resp1.status_code == 200
            assert resp2.status_code == 200

            # Revoke first session
            modules.auth.revoke_session(user.id, login1.session.id)

            # First session fails, second still works
            resp1 = await ac.get("/api/v1/users/@me", headers=headers1)
            resp2 = await ac.get("/api/v1/users/@me", headers=headers2)
            assert resp1.status_code == 401
            assert resp2.status_code == 200

    async def test_revoke_all_sessions(self, modules):
        """Test revoking all sessions via API."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"revokeall_{unique_id}",
            email=f"revokeall_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create multiple sessions
        login1 = modules.auth.login(f"revokeall_{unique_id}", "TestPass123!")
        login2 = modules.auth.login(f"revokeall_{unique_id}", "TestPass123!")
        login3 = modules.auth.login(f"revokeall_{unique_id}", "TestPass123!")

        headers1 = {"Authorization": f"Bearer {login1.token}"}
        headers2 = {"Authorization": f"Bearer {login2.token}"}
        headers3 = {"Authorization": f"Bearer {login3.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Revoke all except current
            resp = await ac.post(
                "/api/v1/auth/sessions/revoke-all",
                headers=headers1,
                json={"except_current": True},
            )
            assert resp.status_code == 200

            # First session still works
            resp1 = await ac.get("/api/v1/users/@me", headers=headers1)
            assert resp1.status_code == 200

            # Other sessions fail
            resp2 = await ac.get("/api/v1/users/@me", headers=headers2)
            resp3 = await ac.get("/api/v1/users/@me", headers=headers3)
            assert resp2.status_code == 401
            assert resp3.status_code == 401


@pytest.mark.asyncio
class TestConcurrentAuthentication:
    """Test concurrent authentication requests for thread safety."""

    async def test_concurrent_authenticated_requests(self, auth_headers):
        """Test multiple concurrent authenticated requests."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.get("/api/v1/users/@me", headers=auth_headers) for _ in range(20)
            ]
            responses = await asyncio.gather(*tasks)

            for resp in responses:
                assert resp.status_code == 200
                data = resp.json()
                assert "id" in data
                assert "username" in data

    async def test_concurrent_mixed_requests(self, auth_headers):
        """Test concurrent requests with valid and invalid tokens."""
        app = create_app()
        valid_headers = auth_headers
        invalid_headers = {"Authorization": "Bearer invalid_token"}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Mix of valid and invalid requests
            tasks = []
            for i in range(10):
                headers = valid_headers if i % 2 == 0 else invalid_headers
                tasks.append(ac.get("/api/v1/users/@me", headers=headers))

            responses = await asyncio.gather(*tasks)

            # Check alternating success/failure
            for i, resp in enumerate(responses):
                if i % 2 == 0:
                    assert resp.status_code == 200
                else:
                    assert resp.status_code == 401

    async def test_concurrent_token_validation_different_users(
        self, modules, session_users
    ):
        """Test concurrent token validation for different users."""
        app = create_app()

        # Get multiple users and their tokens
        tokens = []
        for i in range(5):
            user, username, password = session_users[i]
            result = modules.auth.login(username, password)
            tokens.append(result.token)

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Concurrent requests with different tokens
            tasks = [
                ac.get(
                    "/api/v1/users/@me", headers={"Authorization": f"Bearer {token}"}
                )
                for token in tokens
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed
            for resp in responses:
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestPublicEndpoints:
    """Test endpoints that don't require authentication."""

    async def test_health_endpoint_no_auth(self):
        """Test health endpoint without authentication."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/health")
            assert response.status_code == 200

    async def test_version_negotiate_no_auth(self):
        """Test version negotiation without authentication."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/version/negotiate", json={"client_version": "a.1.0-1"}
            )
            assert response.status_code == 200
            data = response.json()
            assert "compatible" in data

    async def test_password_requirements_no_auth(self):
        """Test password requirements endpoint without authentication."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/auth/password-requirements")
            assert response.status_code == 200
            data = response.json()
            assert "min_length" in data
            assert "require_uppercase" in data


@pytest.mark.asyncio
class TestTokenExpiry:
    """Test token expiry handling."""

    async def test_expired_token_rejected(self, modules):
        """Test that expired tokens are rejected."""
        unique_id = uuid.uuid4().hex[:8]
        modules.auth.register(
            username=f"expiretest_{unique_id}",
            email=f"expiretest_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create a session
        result = modules.auth.login(f"expiretest_{unique_id}", "TestPass123!")
        token = result.token

        # Manually expire the session by updating database
        db = modules._db
        db.execute(
            "UPDATE auth_sessions SET expires_at = datetime('now', '-1 day') WHERE id = ?",
            (result.session.id,),
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 401
            error_msg = response.json()["error"]["message"].lower()
            assert "expired" in error_msg or "invalid" in error_msg
