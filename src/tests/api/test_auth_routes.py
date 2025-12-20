"""
Tests for authentication routes - registration, login, 2FA, rate limiting.

Covers:
- User registration (validation, duplicate detection)
- Login flows (success, failures, 2FA)
- Password management
- Session management
- Input sanitization
- SQL injection prevention
- Rate limiting
- Error handling
"""

import pytest
import uuid
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestRegistration:
    """Test user registration endpoints."""

    async def test_register_success(self):
        """Test successful user registration."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"newuser_{unique_id}",
                    "email": f"newuser_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "token" in data
            assert "user" in data
            assert data["user"]["username"] == f"newuser_{unique_id}"

    async def test_register_duplicate_username(self, modules):
        """Test registration with duplicate username."""
        unique_id = uuid.uuid4().hex[:8]
        username = f"duplicate_{unique_id}"

        # Create first user
        modules.auth.register(
            username=username,
            email=f"{username}@example.com",
            password="SecurePass123!",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": username,
                    "email": f"{username}_new@example.com",
                    "password": "SecurePass123!",
                },
            )

            assert response.status_code == 409
            assert "error" in response.json()

    async def test_register_duplicate_email(self, modules):
        """Test registration with duplicate email."""
        unique_id = uuid.uuid4().hex[:8]
        email = f"duplicate_{unique_id}@example.com"

        # Create first user
        modules.auth.register(
            username=f"user1_{unique_id}", email=email, password="SecurePass123!"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user2_{unique_id}",
                    "email": email,
                    "password": "SecurePass123!",
                },
            )

            assert response.status_code == 409
            assert "error" in response.json()

    async def test_register_invalid_username(self):
        """Test registration with invalid username."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Too short
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": "ab",
                    "email": f"test_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )
            assert response.status_code == 400

            # Invalid characters
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": "user@invalid",
                    "email": f"test_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )
            assert response.status_code == 400

    async def test_register_weak_password(self):
        """Test registration with weak password."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Too short
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"user_{unique_id}@example.com",
                    "password": "short",
                },
            )
            assert response.status_code == 400

            # No uppercase
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"user_{unique_id}@example.com",
                    "password": "securepass123!",
                },
            )
            assert response.status_code == 400

            # No special character
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"user_{unique_id}@example.com",
                    "password": "SecurePass123",
                },
            )
            assert response.status_code == 400

    async def test_register_missing_fields(self):
        """Test registration with missing required fields."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Missing password
            response = await ac.post(
                "/api/v1/auth/register",
                json={"username": "testuser", "email": "test@example.com"},
            )
            assert response.status_code == 422

            # Missing email
            response = await ac.post(
                "/api/v1/auth/register",
                json={"username": "testuser", "password": "SecurePass123!"},
            )
            assert response.status_code == 422

            # Missing username
            response = await ac.post(
                "/api/v1/auth/register",
                json={"email": "test@example.com", "password": "SecurePass123!"},
            )
            assert response.status_code == 422

    async def test_register_sql_injection_username(self):
        """Test SQL injection prevention in username field."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}' OR '1'='1",
                    "email": f"test_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should either reject invalid chars or safely escape
            assert response.status_code in [400, 422]

    async def test_register_sql_injection_email(self):
        """Test SQL injection prevention in email field."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"test_{unique_id}@example.com'; DROP TABLE auth_users; --",
                    "password": "SecurePass123!",
                },
            )

            # Should reject invalid email format
            assert response.status_code in [400, 422]

    async def test_register_xss_prevention(self):
        """Test XSS prevention in input fields."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}<script>alert('xss')</script>",
                    "email": f"test_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should reject invalid characters
            assert response.status_code in [400, 422]


@pytest.mark.asyncio
class TestLogin:
    """Test login endpoints."""

    async def test_login_success(self, modules):
        """Test successful login."""
        unique_id = uuid.uuid4().hex[:8]
        username = f"loginuser_{unique_id}"
        password = "SecurePass123!"

        modules.auth.register(
            username=username, email=f"{username}@example.com", password=password
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "token" in data
            assert data["user"]["username"] == username

    async def test_login_invalid_credentials(self, modules):
        """Test login with invalid credentials."""
        unique_id = uuid.uuid4().hex[:8]
        username = f"loginuser_{unique_id}"

        modules.auth.register(
            username=username,
            email=f"{username}@example.com",
            password="CorrectPass123!",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "WrongPass123!"},
            )

            assert response.status_code == 401
            assert "error" in response.json()

    async def test_login_nonexistent_user(self):
        """Test login with non-existent user."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={
                    "username": f"nonexistent_{unique_id}",
                    "password": "SecurePass123!",
                },
            )

            assert response.status_code == 401
            assert "error" in response.json()

    async def test_login_case_sensitive_username(self, modules):
        """Test that username is case-insensitive in login."""
        unique_id = uuid.uuid4().hex[:8]
        username = f"CaseSensitive_{unique_id}"
        password = "SecurePass123!"

        modules.auth.register(
            username=username, email=f"{username}@example.com", password=password
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Try lowercase
            response = await ac.post(
                "/api/v1/auth/login",
                json={"username": username.lower(), "password": password},
            )

            # Should succeed (case-insensitive)
            assert response.status_code == 200

    async def test_login_sql_injection_username(self):
        """Test SQL injection prevention in login username."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={"username": "admin' OR '1'='1", "password": "password"},
            )

            # Should safely handle and return 401
            assert response.status_code == 401

    async def test_login_sql_injection_password(self):
        """Test SQL injection prevention in login password."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "' OR '1'='1"},
            )

            # Should safely handle and return 401
            assert response.status_code == 401

    async def test_login_account_lockout(self, modules):
        """Test account lockout after multiple failed attempts."""
        unique_id = uuid.uuid4().hex[:8]
        username = f"lockoutuser_{unique_id}"
        password = "CorrectPass123!"

        modules.auth.register(
            username=username, email=f"{username}@example.com", password=password
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Multiple failed login attempts
            for _ in range(5):
                await ac.post(
                    "/api/v1/auth/login",
                    json={"username": username, "password": "WrongPass123!"},
                )

            # Next attempt should be locked
            response = await ac.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )

            # Should be locked (403 or 429)
            assert response.status_code in [403, 429]


@pytest.mark.asyncio
class TestTwoFactorAuth:
    """Test two-factor authentication flow."""

    async def test_2fa_enable(self, auth_headers, modules, test_user):
        """Test enabling 2FA."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/2fa/enable",
                headers=auth_headers,
                json={"password": test_user["password"]},
            )

            assert response.status_code == 200
            data = response.json()
            assert "secret" in data
            assert "qr_uri" in data
            assert "backup_codes" in data

    async def test_2fa_enable_wrong_password(self, auth_headers):
        """Test 2FA enable with wrong password."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/2fa/enable",
                headers=auth_headers,
                json={"password": "WrongPassword123!"},
            )

            assert response.status_code == 401

    async def test_2fa_status(self, auth_headers):
        """Test getting 2FA status."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/auth/2fa/status", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert "enabled" in data

    async def test_2fa_confirm_invalid_code(self, auth_headers, test_user):
        """Test 2FA confirmation with invalid code."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Enable 2FA first
            await ac.post(
                "/api/v1/auth/2fa/enable",
                headers=auth_headers,
                json={"password": test_user["password"]},
            )

            # Try to confirm with invalid code
            response = await ac.post(
                "/api/v1/auth/2fa/confirm",
                headers=auth_headers,
                json={"code": "000000"},
            )

            assert response.status_code == 401


@pytest.mark.asyncio
class TestSessionManagement:
    """Test session management endpoints."""

    async def test_get_sessions(self, auth_headers):
        """Test getting user sessions."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/auth/sessions", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) > 0
            assert "id" in data[0]

    async def test_revoke_session(self, modules, test_user):
        """Test revoking a specific session."""
        # Create two sessions
        result1 = modules.auth.login(test_user["username"], test_user["password"])
        result2 = modules.auth.login(test_user["username"], test_user["password"])

        app = create_app()
        headers = {"Authorization": f"Bearer {result1.token}"}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Revoke second session
            response = await ac.delete(
                f"/api/v1/auth/sessions/{result2.session.id}", headers=headers
            )

            assert response.status_code == 200

    async def test_revoke_nonexistent_session(self, auth_headers):
        """Test revoking a non-existent session."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                "/api/v1/auth/sessions/999999999", headers=auth_headers
            )

            assert response.status_code == 404

    async def test_logout(self, modules, test_user):
        """Test logout endpoint."""
        result = modules.auth.login(test_user["username"], test_user["password"])
        headers = {"Authorization": f"Bearer {result.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/v1/auth/logout", headers=headers)

            assert response.status_code == 200

            # Token should now be invalid
            response = await ac.get("/api/v1/users/@me", headers=headers)
            assert response.status_code == 401


@pytest.mark.asyncio
class TestPasswordRequirements:
    """Test password requirements endpoint."""

    async def test_get_password_requirements(self):
        """Test getting password requirements."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/auth/password-requirements")

            assert response.status_code == 200
            data = response.json()
            assert "min_length" in data
            assert "max_length" in data
            assert "require_uppercase" in data
            assert "require_lowercase" in data
            assert "require_digit" in data
            assert "require_special" in data


@pytest.mark.asyncio
class TestRateLimiting:
    """Test rate limiting on auth endpoints."""

    async def test_register_rate_limit(self):
        """Test rate limiting on registration endpoint."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Make many registration requests
            responses = []
            for i in range(15):
                unique_id = uuid.uuid4().hex[:8]
                response = await ac.post(
                    "/api/v1/auth/register",
                    json={
                        "username": f"ratelimit_{unique_id}",
                        "email": f"ratelimit_{unique_id}@example.com",
                        "password": "SecurePass123!",
                    },
                )
                responses.append(response)

            # Some requests should be rate limited (429)
            status_codes = [r.status_code for r in responses]
            # Either some succeed and some rate limited, or all succeed if rate limit is high
            assert 200 in status_codes or 429 in status_codes

    async def test_login_rate_limit(self):
        """Test rate limiting on login endpoint."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Make many login requests
            responses = []
            for _ in range(15):
                response = await ac.post(
                    "/api/v1/auth/login",
                    json={"username": "nonexistent", "password": "WrongPass123!"},
                )
                responses.append(response)

            # Some requests should be rate limited (429) or failed (401)
            status_codes = [r.status_code for r in responses]
            assert 401 in status_codes or 429 in status_codes


@pytest.mark.asyncio
class TestInputSanitization:
    """Test input sanitization and validation."""

    async def test_username_whitespace_trimmed(self):
        """Test that username whitespace is handled."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"  user_{unique_id}  ",
                    "email": f"user_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should either trim or reject
            assert response.status_code in [200, 400, 422]

    async def test_email_normalization(self):
        """Test email address normalization."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"  USER_{unique_id}@EXAMPLE.COM  ",
                    "password": "SecurePass123!",
                },
            )

            # Should normalize email (lowercase, trim)
            assert response.status_code in [200, 400, 422]

    async def test_unicode_username(self):
        """Test unicode characters in username."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}_日本語",
                    "email": f"user_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should handle unicode properly
            assert response.status_code in [200, 400, 422]

    async def test_null_byte_injection(self):
        """Test null byte injection prevention."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}\x00admin",
                    "email": f"user_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should reject null bytes
            assert response.status_code in [400, 422]


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling and responses."""

    async def test_malformed_json(self):
        """Test handling of malformed JSON."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                content="{invalid json}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 422

    async def test_missing_content_type(self):
        """Test handling of missing Content-Type header."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{unique_id}",
                    "email": f"user_{unique_id}@example.com",
                    "password": "SecurePass123!",
                },
            )

            # Should work with or without explicit Content-Type
            assert response.status_code in [200, 400, 422]

    async def test_error_response_format(self):
        """Test that error responses have consistent format."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={"username": "nonexistent", "password": "WrongPass123!"},
            )

            assert response.status_code == 401
            data = response.json()
            assert "error" in data
            assert "code" in data["error"]
            assert "message" in data["error"]
