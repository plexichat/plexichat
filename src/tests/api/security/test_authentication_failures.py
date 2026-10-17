"""Tests for authentication failure scenarios."""

from fastapi import status


def test_login_with_invalid_credentials(test_client):
    """Test that login fails with invalid credentials."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent", "password": "wrongpassword"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 401
    assert "Invalid credentials" in data["error"]["message"]


def test_login_with_missing_fields(test_client):
    """Test that login fails with missing required fields."""
    # Missing password
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_register_with_duplicate_username(test_client, auth_manager):
    """Test that registration fails with duplicate username."""
    from src.utils import encryption
    from unittest.mock import patch

    # Register first user
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username="takenuser",
            email="taken@example.com",
            password="TestPass123!",
        )

    # Try to register with same username
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "takenuser",
            "email": "different@example.com",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 409
    assert "already exists" in data["error"]["message"]


def test_register_with_weak_password(test_client):
    """Test that registration fails with weak password."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "weak",  # Too short, no complexity
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 400


def test_register_with_invalid_email(test_client):
    """Test that registration fails with invalid email."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "notanemail",
            "password": "TestPass123!",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == 400


def test_protected_route_without_auth(test_client):
    """Test that protected routes fail without authentication."""
    response = test_client.get("/api/v1/users/@me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_protected_route_with_invalid_token(test_client):
    """Test that protected routes fail with invalid token."""
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": "Bearer invalid_token_12345"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
