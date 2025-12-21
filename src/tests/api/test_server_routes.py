"""
Tests for server routes.
"""

import uuid


class TestGetServers:
    """Tests for GET /servers endpoint."""

    def test_get_servers_success(self, test_client, auth_headers, test_server):
        """Test getting user's servers."""
        response = test_client.get("/api/v1/servers", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_servers_without_auth(self, test_client):
        """Test getting servers without authentication."""
        response = test_client.get("/api/v1/servers")

        assert response.status_code == 401


class TestCreateServer:
    """Tests for POST /servers endpoint."""

    def test_create_server_success(self, test_client, auth_headers):
        """Test creating a server."""
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/servers",
            headers=auth_headers,
            json={"name": f"New Server {unique_id}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == f"New Server {unique_id}"
        assert "id" in data
        assert "owner_id" in data

    def test_create_server_with_description(self, test_client, auth_headers):
        """Test creating a server with description."""
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/servers",
            headers=auth_headers,
            json={
                "name": f"Described Server {unique_id}",
                "description": "A test server with description"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "A test server with description"

    def test_create_server_without_auth(self, test_client):
        """Test creating server without authentication."""
        response = test_client.post(
            "/api/v1/servers",
            json={"name": "Unauthorized Server"}
        )

        assert response.status_code == 401

    def test_create_server_empty_name(self, test_client, auth_headers):
        """Test creating server with empty name."""
        response = test_client.post(
            "/api/v1/servers",
            headers=auth_headers,
            json={"name": ""}
        )

        assert response.status_code == 400 or response.status_code == 422


class TestGetServer:
    """Tests for GET /servers/{server_id} endpoint."""

    def test_get_server_success(self, test_client, auth_headers, test_server):
        """Test getting server by ID."""
        server_id = str(test_server["server"].id)

        response = test_client.get(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == server_id

    def test_get_nonexistent_server(self, test_client, auth_headers):
        """Test getting nonexistent server."""
        response = test_client.get(
            "/api/v1/servers/999999999999999999",
            headers=auth_headers
        )

        assert response.status_code == 404

    def test_get_server_invalid_id(self, test_client, auth_headers):
        """Test getting server with invalid ID."""
        response = test_client.get(
            "/api/v1/servers/invalid_id",
            headers=auth_headers
        )

        assert response.status_code == 400


class TestUpdateServer:
    """Tests for PATCH /servers/{server_id} endpoint."""

    def test_update_server_name(self, test_client, auth_headers, test_server):
        """Test updating server name."""
        server_id = str(test_server["server"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.patch(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers,
            json={"name": f"Updated Server {unique_id}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == f"Updated Server {unique_id}"

    def test_update_server_description(self, test_client, auth_headers, test_server):
        """Test updating server description."""
        server_id = str(test_server["server"].id)

        response = test_client.patch(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers,
            json={"description": "Updated description"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"


class TestDeleteServer:
    """Tests for DELETE /servers/{server_id} endpoint."""

    def test_delete_server_success(self, test_client, db_and_modules):
        """Test deleting a server."""
        auth = db_and_modules["auth"]
        servers = db_and_modules["servers"]
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"deleteserver_{unique_id}",
            email=f"deleteserver_{unique_id}@example.com",
            password="SecurePass123!"
        )

        result = auth.login(
            username=f"deleteserver_{unique_id}",
            password="SecurePass123!"
        )

        server = servers.create_server(user.id, f"To Delete {unique_id}")

        response = test_client.delete(
            f"/api/v1/servers/{server.id}",
            headers={"Authorization": f"Bearer {result.token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestGetServerChannels:
    """Tests for GET /servers/{server_id}/channels endpoint."""

    def test_get_channels_success(self, test_client, auth_headers, test_server):
        """Test getting server channels."""
        server_id = str(test_server["server"].id)

        response = test_client.get(
            f"/api/v1/servers/{server_id}/channels",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_channels_nonexistent_server(self, test_client, auth_headers):
        """Test getting channels for nonexistent server."""
        response = test_client.get(
            "/api/v1/servers/999999999999999999/channels",
            headers=auth_headers
        )

        assert response.status_code == 404


class TestServerMembership:
    """Tests for server membership."""

    def test_server_has_owner_id(self, test_client, auth_headers, test_server):
        """Test that server response includes owner_id."""
        server_id = str(test_server["server"].id)

        response = test_client.get(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "owner_id" in data
        assert data["owner_id"] is not None

    def test_server_has_member_count(self, test_client, auth_headers, test_server):
        """Test that server response includes member_count."""
        server_id = str(test_server["server"].id)

        response = test_client.get(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "member_count" in data

    def test_server_has_created_at(self, test_client, auth_headers, test_server):
        """Test that server response includes created_at."""
        server_id = str(test_server["server"].id)

        response = test_client.get(
            f"/api/v1/servers/{server_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "created_at" in data
        assert data["created_at"] > 0
