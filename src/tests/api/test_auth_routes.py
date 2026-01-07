"""
Tests for authentication routes.
"""

import uuid


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
                "password": "SecurePass123!",
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
                "password": "SecurePass123!",
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
                "password": "SecurePass123!",
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
            json={"username": test_user["username"], "password": "WrongPassword123!"},
        )

        assert response.status_code == 401

    def test_login_nonexistent_user(self, test_client):
        """Test login with nonexistent user."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent_user_12345", "password": "SomePassword123!"},
        )

        assert response.status_code == 401

    def test_login_with_email(self, test_client, db_and_modules):
        """Test login using email instead of username."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        auth.register(
            username=f"emaillogin_{unique_id}",
            email=f"emaillogin_{unique_id}@example.com",
            password="SecurePass123!",
        )

        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": f"emaillogin_{unique_id}@example.com",
                "password": "SecurePass123!",
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

        result = auth.login(username=f"logout_{unique_id}", password="SecurePass123!")

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
