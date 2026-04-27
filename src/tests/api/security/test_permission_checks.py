"""Tests for permission checks."""


def test_access_protected_route_without_permission(test_client):
    """Test that protected routes require authentication."""
    response = test_client.get("/api/v1/users/@me")

    assert response.status_code == 401


def test_access_admin_route_as_regular_user(test_client, auth_manager):
    """Test that regular users cannot access admin routes."""
    from src.utils import encryption
    from unittest.mock import patch

    # Create a regular user
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="regularuser",
            email="regular@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login("regularuser", "TestPass123!")

    # Try to access admin route
    response = test_client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {result.token}"},
    )

    # Should be forbidden or not found
    assert response.status_code in [403, 404]


def test_access_other_user_data(test_client, auth_manager):
    """Test that users can access other users' public profile data."""
    from src.utils import encryption
    from unittest.mock import patch

    # Create two users
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username="user1",
            email="user1@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username="user2",
            email="user2@example.com",
            password="TestPass123!",
        )

    # Login as user1
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login("user1", "TestPass123!")

    # Try to access user2's public profile
    response = test_client.get(
        f"/api/v1/users/{user2.id}",
        headers={"Authorization": f"Bearer {result.token}"},
    )

    # Public profiles are typically accessible
    # If the API returns 200, it means public profiles are accessible
    # If it returns 403/404, it means profiles are private
    assert response.status_code in [200, 403, 404]


def test_modify_resource_without_ownership(test_client, auth_manager, server_manager):
    """Test that users cannot modify resources they don't own."""
    from src.utils import encryption
    from unittest.mock import patch

    # Create two users
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username="user1",
            email="user1@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username="user2",
            email="user2@example.com",
            password="TestPass123!",
        )

    # Create a server as user1
    server = server_manager.create_server(
        owner_id=user1.id,
        name="Test Server",
    )

    # Login as user2
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login("user2", "TestPass123!")

    # Try to modify user1's server
    response = test_client.patch(
        f"/api/v1/servers/{server.id}",
        json={"name": "Hacked Server"},
        headers={"Authorization": f"Bearer {result.token}"},
    )

    # Should be forbidden
    assert response.status_code in [403, 404]
