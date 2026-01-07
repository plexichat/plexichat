"""
Tests for presence routes.
"""


class TestUpdatePresence:
    """Tests for PUT /users/@me/presence endpoint."""

    def test_update_presence_online(self, test_client, auth_headers):
        """Test setting presence to online."""
        response = test_client.put(
            "/api/v1/users/@me/presence",
            headers=auth_headers,
            json={"status": "online"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"

    def test_update_presence_idle(self, test_client, auth_headers):
        """Test setting presence to idle."""
        response = test_client.put(
            "/api/v1/users/@me/presence", headers=auth_headers, json={"status": "idle"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "idle"

    def test_update_presence_dnd(self, test_client, auth_headers):
        """Test setting presence to do not disturb."""
        response = test_client.put(
            "/api/v1/users/@me/presence", headers=auth_headers, json={"status": "dnd"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dnd"

    def test_update_presence_invisible(self, test_client, auth_headers):
        """Test setting presence to invisible."""
        response = test_client.put(
            "/api/v1/users/@me/presence",
            headers=auth_headers,
            json={"status": "invisible"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "invisible"

    def test_update_presence_with_custom_status(self, test_client, auth_headers):
        """Test setting presence with custom status."""
        response = test_client.put(
            "/api/v1/users/@me/presence",
            headers=auth_headers,
            json={"status": "online", "custom_status": "Working on something cool"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert data["custom_status"] == "Working on something cool"

    def test_update_presence_invalid_status(self, test_client, auth_headers):
        """Test setting invalid presence status."""
        response = test_client.put(
            "/api/v1/users/@me/presence",
            headers=auth_headers,
            json={"status": "invalid_status"},
        )

        assert response.status_code == 400

    def test_update_presence_without_auth(self, test_client):
        """Test updating presence without authentication."""
        response = test_client.put(
            "/api/v1/users/@me/presence", json={"status": "online"}
        )

        assert response.status_code == 401


class TestGetUserPresence:
    """Tests for GET /users/{user_id}/presence endpoint."""

    def test_get_user_presence(self, test_client, auth_headers, test_user):
        """Test getting user presence."""
        user_id = str(test_user["user"].id)

        response = test_client.get(
            f"/api/v1/users/{user_id}/presence", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user_id
        assert "status" in data

    def test_get_nonexistent_user_presence(self, test_client, auth_headers):
        """Test getting presence for nonexistent user."""
        response = test_client.get(
            "/api/v1/users/999999999999999999/presence", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "offline"

    def test_get_presence_without_auth(self, test_client, test_user):
        """Test getting presence without authentication."""
        user_id = str(test_user["user"].id)

        response = test_client.get(f"/api/v1/users/{user_id}/presence")

        assert response.status_code == 401
