"""
Tests for authentication routes.
"""

from fastapi import status


def test_register_success(test_client):
    """Test successful user registration."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "token" in data
    assert "user" in data
    assert data["user"]["username"] == "newuser"


def test_login_success(test_client, auth_manager):
    """Test successful user login."""
    from src.utils import encryption
    from unittest.mock import patch

    # Register a user first
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username="loginuser",
            email="loginuser@example.com",
            password="TestPass123!",
        )

    # Login
    response = test_client.post(
        "/api/v1/auth/login",
        json={
            "username": "loginuser",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "token" in data
    assert "user" in data
    assert data["user"]["username"] == "loginuser"


def test_register_duplicate_username(test_client, auth_manager):
    """Test registration with duplicate username fails."""
    from src.utils import encryption
    from unittest.mock import patch

    # Register first user
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username="duplicate",
            email="first@example.com",
            password="TestPass123!",
        )

    # Try to register with same username
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "duplicate",
            "email": "second@example.com",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 409


def test_register_weak_password(test_client):
    """Test registration with weak password fails."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "weakuser",
            "email": "weak@example.com",
            "password": "weak",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 400


def test_register_invalid_email(test_client):
    """Test registration with invalid email fails."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "bademail",
            "email": "notanemail",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 400


def test_login_invalid_credentials(test_client):
    """Test login with invalid credentials fails."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={
            "username": "nonexistent",
            "password": "wrongpassword",
        },
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 401


def test_login_missing_fields(test_client):
    """Test login with missing fields fails."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST  # Validation error
