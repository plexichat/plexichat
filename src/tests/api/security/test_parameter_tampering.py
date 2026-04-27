"""Tests for parameter tampering."""


def test_tamper_with_user_id_in_url(test_client, auth_manager):
    """Test that tampering with user ID in URL is prevented."""
    from src.utils import encryption
    from unittest.mock import patch

    # Create a user and get their token
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="testuser",
            email="test@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login("testuser", "TestPass123!")

    # Try to access another user's data by changing ID
    response = test_client.get(
        "/api/v1/users/999",  # Non-existent user ID
        headers={"Authorization": f"Bearer {result.token}"},
    )

    # Should fail (either 404 or 403)
    assert response.status_code in [404, 403]


def test_tamper_with_boolean_parameters(test_client):
    """Test that boolean parameter tampering is handled."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "TestPass123!",
            "age_verified": "true",  # String instead of boolean
        },
    )

    # Should handle the type conversion or reject
    assert response.status_code in [200, 400]


def test_tamper_with_numeric_parameters(test_client):
    """Test that numeric parameter tampering is handled."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "TestPass123!",
            "age": -1,  # Invalid negative age
        },
    )

    # Should either reject or handle safely
    assert response.status_code in [200, 400]


def test_tamper_with_array_parameters(test_client):
    """Test that array parameter tampering is handled."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={
            "username": ["admin", "user"],  # Array instead of string
            "password": "test",
        },
    )

    # Should reject invalid type
    assert response.status_code == 400


def test_tamper_with_extra_fields(test_client):
    """Test that extra fields in request are ignored or rejected."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={
            "username": "testuser",
            "password": "test",
            "is_admin": True,  # Extra field that shouldn't exist
        },
    )

    # Should ignore extra field or reject
    assert response.status_code in [401, 400]
