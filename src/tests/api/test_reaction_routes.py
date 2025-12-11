"""
Tests for reaction routes.
"""



class TestAddReaction:
    """Tests for PUT /channels/{channel_id}/messages/{message_id}/reactions/{emoji} endpoint."""

    def test_add_reaction_success(self, test_client, auth_headers, test_server):
        """Test adding a reaction to a message."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "React to this!"}
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        response = test_client.put(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/thumbsup",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_add_reaction_nonexistent_message(self, test_client, auth_headers, test_server):
        """Test adding reaction to nonexistent message."""
        channel_id = str(test_server["channel"].id)

        response = test_client.put(
            f"/api/v1/channels/{channel_id}/messages/999999999999999999/reactions/thumbsup",
            headers=auth_headers
        )

        assert response.status_code == 404

    def test_add_reaction_without_auth(self, test_client, test_server):
        """Test adding reaction without authentication."""
        channel_id = str(test_server["channel"].id)

        response = test_client.put(
            f"/api/v1/channels/{channel_id}/messages/123/reactions/thumbsup"
        )

        assert response.status_code == 401


class TestRemoveReaction:
    """Tests for DELETE /channels/{channel_id}/messages/{message_id}/reactions/{emoji} endpoint."""

    def test_remove_reaction_success(self, test_client, auth_headers, test_server, db_and_modules):
        """Test removing a reaction from a message."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Remove reaction from this!"}
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        test_client.put(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/thumbsup",
            headers=auth_headers
        )

        response = test_client.delete(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/thumbsup",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_remove_nonexistent_reaction(self, test_client, auth_headers, test_server):
        """Test removing reaction that doesn't exist."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "No reactions here"}
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        response = test_client.delete(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/thumbsup",
            headers=auth_headers
        )

        assert response.status_code == 200


class TestGetReactions:
    """Tests for GET /channels/{channel_id}/messages/{message_id}/reactions endpoint."""

    def test_get_reactions_success(self, test_client, auth_headers, test_server):
        """Test getting all reactions on a message."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Get reactions from this!"}
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        test_client.put(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/thumbsup",
            headers=auth_headers
        )

        response = test_client.get(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["emoji"] == "thumbsup"
        assert data[0]["count"] >= 1
        assert data[0]["me"] is True


class TestGetReactionUsers:
    """Tests for GET /channels/{channel_id}/messages/{message_id}/reactions/{emoji} endpoint."""

    def test_get_reaction_users_success(self, test_client, auth_headers, test_server):
        """Test getting users who reacted with an emoji."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Get reaction users!"}
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        test_client.put(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/heart",
            headers=auth_headers
        )

        response = test_client.get(
            f"/api/v1/channels/{channel_id}/messages/{message_id}/reactions/heart",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "user_id" in data[0]
        assert "reacted_at" in data[0]
