"""
Tests for relationship routes.
"""

import uuid


class TestGetRelationships:
    """Tests for GET /relationships/@me endpoint."""

    def test_get_relationships_success(self, test_client, auth_headers):
        """Test getting user relationships."""
        response = test_client.get("/api/v1/relationships/@me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_relationships_without_auth(self, test_client):
        """Test getting relationships without authentication."""
        response = test_client.get("/api/v1/relationships/@me")

        assert response.status_code == 401


class TestSendFriendRequest:
    """Tests for POST /relationships endpoint."""

    def test_send_friend_request_success(
        self, test_client, auth_headers, db_and_modules
    ):
        """Test sending a friend request."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        target = auth.register(
            username=f"friendtarget_{unique_id}",
            email=f"friendtarget_{unique_id}@example.com",
            password="SecurePass123!",
        )

        response = test_client.post(
            "/api/v1/relationships",
            headers=auth_headers,
            json={"user_id": str(target.id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_outgoing"
        assert data["user_id"] == str(target.id)

    def test_send_friend_request_to_self(self, test_client, auth_headers, test_user):
        """Test sending friend request to self."""
        response = test_client.post(
            "/api/v1/relationships",
            headers=auth_headers,
            json={"user_id": str(test_user["user"].id)},
        )

        assert response.status_code == 400

    def test_send_friend_request_nonexistent_user(self, test_client, auth_headers):
        """Test sending friend request to nonexistent user."""
        response = test_client.post(
            "/api/v1/relationships",
            headers=auth_headers,
            json={"user_id": "999999999999999999"},
        )

        assert response.status_code == 404


class TestAcceptFriendRequest:
    """Tests for PUT /relationships/{user_id}/accept endpoint."""

    def test_accept_friend_request_success(self, test_client, db_and_modules):
        """Test accepting a friend request."""
        auth = db_and_modules["auth"]
        relationships = db_and_modules["relationships"]
        unique_id = uuid.uuid4().hex[:8]

        sender = auth.register(
            username=f"sender_{unique_id}",
            email=f"sender_{unique_id}@example.com",
            password="SecurePass123!",
        )

        recipient = auth.register(
            username=f"recipient_{unique_id}",
            email=f"recipient_{unique_id}@example.com",
            password="SecurePass123!",
        )

        relationships.send_friend_request(sender.id, recipient.id)

        result = auth.login(
            username=f"recipient_{unique_id}", password="SecurePass123!"
        )

        response = test_client.put(
            f"/api/v1/relationships/{sender.id}/accept",
            headers={"Authorization": f"Bearer {result.token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestDeleteRelationship:
    """Tests for DELETE /relationships/{user_id} endpoint."""

    def test_remove_friend(self, test_client, db_and_modules):
        """Test removing a friend."""
        auth = db_and_modules["auth"]
        relationships = db_and_modules["relationships"]
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"friend1_{unique_id}",
            email=f"friend1_{unique_id}@example.com",
            password="SecurePass123!",
        )

        user2 = auth.register(
            username=f"friend2_{unique_id}",
            email=f"friend2_{unique_id}@example.com",
            password="SecurePass123!",
        )

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.accept_friend_request(user2.id, request.id)

        result = auth.login(username=f"friend1_{unique_id}", password="SecurePass123!")

        response = test_client.delete(
            f"/api/v1/relationships/{user2.id}",
            headers={"Authorization": f"Bearer {result.token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestBlockUser:
    """Tests for POST /relationships/block endpoint."""

    def test_block_user_success(self, test_client, auth_headers, db_and_modules):
        """Test blocking a user."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        target = auth.register(
            username=f"blocktarget_{unique_id}",
            email=f"blocktarget_{unique_id}@example.com",
            password="SecurePass123!",
        )

        response = test_client.post(
            "/api/v1/relationships/block",
            headers=auth_headers,
            json={"user_id": str(target.id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "blocked"
        assert data["user_id"] == str(target.id)

    def test_block_self(self, test_client, auth_headers, test_user):
        """Test blocking self."""
        response = test_client.post(
            "/api/v1/relationships/block",
            headers=auth_headers,
            json={"user_id": str(test_user["user"].id)},
        )

        assert response.status_code == 400
