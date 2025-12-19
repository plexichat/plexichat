"""
Tests for authentication middleware.
"""

import uuid


import pytest
import asyncio
import uuid
from httpx import AsyncClient
from src.api.app import create_app

@pytest.mark.asyncio
class TestAuthenticationAsync:
    """Enhanced asynchronous authentication tests."""

    async def test_valid_bearer_token(self, auth_headers):
        """Test request with valid Bearer token."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me", headers=auth_headers)
            assert response.status_code == 200

    async def test_invalid_bearer_token(self):
        """Test request with invalid Bearer token."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me",
                headers={"Authorization": "Bearer invalid_token_12345"}
            )
            assert response.status_code == 401
            assert "error" in response.json()

    async def test_concurrent_authenticated_requests(self, auth_headers):
        """Test multiple concurrent authenticated requests to verify thread safety and performance."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [ac.get("/api/v1/users/@me", headers=auth_headers) for _ in range(20)]
            responses = await asyncio.gather(*tasks)
            
            for resp in responses:
                assert resp.status_code == 200

    async def test_bot_token_integration(self, db_and_modules):
        """Test request with Bot token scheme and verify permissions."""
        db, auth, messaging, servers, rel, pres = db_and_modules
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"botowner_{unique_id}",
            email=f"botowner_{unique_id}@example.com",
            password="SecurePass123!"
        )

        bot = auth.create_bot(
            owner_id=user.id,
            username=f"testbot_{unique_id}",
            display_name=f"Test Bot {unique_id}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me",
                headers={"Authorization": f"Bot {bot.token}"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["username"] == f"testbot_{unique_id}"

    async def test_token_revocation_propagation(self, db_and_auth):
        """Test that token revocation is immediate across API requests."""
        db, auth = db_and_auth
        user = auth.register("revoketest", "revoketest@example.com", "TestPass123!")
        login_result = auth.login("revoketest", "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # First request should succeed
            resp1 = await ac.get("/api/v1/users/@me", headers=headers)
            assert resp1.status_code == 200
            
            # Revoke session
            auth.revoke_session(user.id, login_result.session.id)
            
            # Second request should fail immediately
            resp2 = await ac.get("/api/v1/users/@me", headers=headers)
            assert resp2.status_code == 401
            assert "revoked" in resp2.json()["error"]["message"].lower()

@pytest.mark.asyncio
class TestPublicEndpointsAsync:
    """Tests for asynchronous public endpoints."""

    async def test_health_and_version_negotiation(self):
        """Test health and version negotiation without auth."""
        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Test health
            health_resp = await ac.get("/api/v1/health")
            assert health_resp.status_code == 200
            
            # Test version negotiation
            neg_resp = await ac.post("/api/v1/version/negotiate", json={
                "client_version": "a.1.0-1"
            })
            assert neg_resp.status_code == 200
            assert neg_resp.json()["compatible"] is True

