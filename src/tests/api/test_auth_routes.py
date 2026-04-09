"""
Tests for authentication routes.
"""

import asyncio
import httpx
import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import src.api.routes.auth as auth_routes
from src.api.schemas.auth import OAuthCallbackRequest
from starlette.requests import Request


class _FakeAsyncResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "request failed", request=request, response=response
            )


class _FakeAsyncClient:
    def __init__(self, token_result, user_info, emails=None):
        self._token_result = token_result
        self._user_info = user_info
        self._emails = emails or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        return _FakeAsyncResponse(200, self._token_result)

    async def get(self, url, headers=None):
        if url == "https://api.github.com/user/emails":
            return _FakeAsyncResponse(200, self._emails)
        return _FakeAsyncResponse(200, self._user_info)


def _build_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class TestRegister:
    """Tests for user registration endpoint."""

    def test_register_success(self, test_client):
        """Test successful user registration."""
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": f"newuser_{unique_id}",
                "email": f"newuser_{unique_id}@example.com",
                "password": "SecurePass123!",  # pragma: allowlist secret
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "token" in data
        assert data["user"]["username"] == f"newuser_{unique_id}"

    def test_register_duplicate_username(self, test_client, test_user):
        """Test registration with existing username."""
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": test_user["username"],
                "email": "different@example.com",
                "password": "SecurePass123!",  # pragma: allowlist secret
            },
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data

    def test_register_weak_password(self, test_client):
        """Test registration with weak password."""
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": f"weakpwd_{unique_id}",
                "email": f"weakpwd_{unique_id}@example.com",
                "password": "weak",
            },
        )

        assert response.status_code == 400

    def test_register_invalid_email(self, test_client):
        """Test registration with invalid email."""
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": f"invalidemail_{unique_id}",
                "email": "not-an-email",
                "password": "SecurePass123!",  # pragma: allowlist secret
            },
        )

        assert response.status_code == 400 or response.status_code == 422


class TestLogin:
    """Tests for user login endpoint."""

    def test_login_success(self, test_client, test_user):
        """Test successful login."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": test_user["username"], "password": test_user["password"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "token" in data
        assert data["user"]["username"] == test_user["username"]

    def test_login_wrong_password(self, test_client, test_user):
        """Test login with wrong password."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": test_user["username"],
                "password": "WrongPassword123!",
            },  # pragma: allowlist secret
        )

        assert response.status_code == 401

    def test_login_nonexistent_user(self, test_client):
        """Test login with nonexistent user."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": "nonexistent_user_12345",
                "password": "SomePassword123!",
            },  # pragma: allowlist secret
        )

        assert response.status_code == 401

    def test_login_with_email(self, test_client, db_and_modules):
        """Test login using email instead of username."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        auth.register(
            username=f"emaillogin_{unique_id}",
            email=f"emaillogin_{unique_id}@example.com",
            password="SecurePass123!",  # pragma: allowlist secret
        )

        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": f"emaillogin_{unique_id}@example.com",
                "password": "SecurePass123!",  # pragma: allowlist secret
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


class TestLogout:
    """Tests for user logout endpoint."""

    def test_logout_success(self, test_client, db_and_modules):
        """Test successful logout."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        auth.register(
            username=f"logout_{unique_id}",
            email=f"logout_{unique_id}@example.com",
            password="SecurePass123!",
        )

        result = auth.login(
            username=f"logout_{unique_id}", password="SecurePass123!"
        )  # pragma: allowlist secret

        response = test_client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {result.token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_logout_without_auth(self, test_client):
        """Test logout without authentication."""
        response = test_client.post("/api/v1/auth/logout")

        assert response.status_code == 401

    def test_logout_invalid_token(self, test_client):
        """Test logout with invalid token."""
        response = test_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer invalid_token_12345"},
        )

        assert response.status_code == 401


class TestTwoFactorAuth:
    """Tests for 2FA endpoints."""

    def test_2fa_invalid_challenge_token(self, test_client):
        """Test 2FA with invalid challenge token."""
        response = test_client.post(
            "/api/v1/auth/2fa",
            json={"challenge_token": "invalid_challenge_token", "code": "123456"},
        )

        assert response.status_code == 401

    def test_2fa_missing_code(self, test_client):
        """Test 2FA with missing code."""
        response = test_client.post(
            "/api/v1/auth/2fa", json={"challenge_token": "some_token"}
        )

        assert response.status_code == 400 or response.status_code == 422


class TestOAuthCallbackSecurity:
    """Tests for OAuth callback security checks."""

    def test_google_callback_rejects_unverified_email(self, monkeypatch):
        """Test Google OAuth callback requires a verified email address."""
        fake_auth = MagicMock()

        monkeypatch.setattr(
            auth_routes,
            "verify_oauth_state",
            lambda **kwargs: (True, SimpleNamespace(pkce_challenge=None), None),
        )
        monkeypatch.setattr(auth_routes.api, "get_auth", lambda: fake_auth)
        monkeypatch.setattr(
            auth_routes,
            "config_util",
            SimpleNamespace(
                get=lambda key, default=None: {
                    "google": {"client_id": "client", "client_secret": "secret"}
                }
                if key == "oauth"
                else default
            ),
        )
        monkeypatch.setattr(
            auth_routes.httpx,
            "AsyncClient",
            lambda: _FakeAsyncClient(
                {"access_token": "token"},
                {
                    "sub": "google-user-1",
                    "email": "user@example.com",
                    "email_verified": False,
                    "name": "Example User",
                },
            ),
        )

        with pytest.raises(auth_routes.HTTPException) as exc_info:
            asyncio.run(
                auth_routes.oauth_callback(
                    _build_request("/api/v1/auth/oauth/google/callback"),
                    "google",
                    OAuthCallbackRequest(
                        code="oauth-code",
                        state="state-token",
                        redirect_uri="https://app.example.com/oauth/google/callback",
                    ),
                )
            )

        assert exc_info.value.status_code == 400
        assert (
            exc_info.value.detail["error"]["message"]
            == "Google account email must be verified"
        )
        fake_auth.oauth_login.assert_not_called()

    def test_github_callback_rejects_accounts_without_verified_email(self, monkeypatch):
        """Test GitHub OAuth callback requires a verified email address."""
        fake_auth = MagicMock()

        monkeypatch.setattr(
            auth_routes,
            "verify_oauth_state",
            lambda **kwargs: (True, SimpleNamespace(pkce_challenge=None), None),
        )
        monkeypatch.setattr(auth_routes.api, "get_auth", lambda: fake_auth)
        monkeypatch.setattr(
            auth_routes,
            "config_util",
            SimpleNamespace(
                get=lambda key, default=None: {
                    "github": {"client_id": "client", "client_secret": "secret"}
                }
                if key == "oauth"
                else default
            ),
        )
        monkeypatch.setattr(
            auth_routes.httpx,
            "AsyncClient",
            lambda: _FakeAsyncClient(
                {"access_token": "token"},
                {
                    "id": 42,
                    "login": "octocat",
                    "email": None,
                    "verified": False,
                },
                emails=[
                    {
                        "email": "octocat@example.com",
                        "verified": False,
                        "primary": True,
                    }
                ],
            ),
        )

        with pytest.raises(auth_routes.HTTPException) as exc_info:
            asyncio.run(
                auth_routes.oauth_callback(
                    _build_request("/api/v1/auth/oauth/github/callback"),
                    "github",
                    OAuthCallbackRequest(
                        code="oauth-code",
                        state="state-token",
                        redirect_uri="https://app.example.com/oauth/github/callback",
                    ),
                )
            )

        assert exc_info.value.status_code == 400
        assert (
            exc_info.value.detail["error"]["message"]
            == "GitHub account must expose a verified email"
        )
        fake_auth.oauth_login.assert_not_called()
