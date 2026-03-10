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

    def test_get_relationships_uses_public_profiles(
        self, test_client, db_and_modules, monkeypatch
    ):
        """Test relationships endpoint uses public profile lookup instead of full users."""
        auth = db_and_modules["auth"]
        relationships = db_and_modules["relationships"]
        unique_id = uuid.uuid4().hex[:8]

        requester = auth.register(
            username=f"relrequester_{unique_id}",
            email=f"relrequester_{unique_id}@example.com",
            password="SecurePass123!",
        )
        friend = auth.register(
            username=f"relfriend_{unique_id}",
            email=f"relfriend_{unique_id}@example.com",
            password="SecurePass123!",
        )
        request = relationships.send_friend_request(requester.id, friend.id)
        relationships.accept_friend_request(friend.id, request.id)
        login = auth.login(username=f"relrequester_{unique_id}", password="SecurePass123!")

        def fail_get_users_bulk(*args, **kwargs):
            raise AssertionError("get_users_bulk should not be used for relationship listings")

        def fake_profiles(user_ids):
            assert friend.id in user_ids
            return {
                str(friend.id): {
                    "id": friend.id,
                    "username": f"publicfriend_{unique_id}",
                    "created_at": friend.created_at,
                    "avatar_url": f"/api/v1/avatars/users/{friend.id}",
                    "badges": [],
                }
            }

        monkeypatch.setattr(auth, "get_users_bulk", fail_get_users_bulk)
        monkeypatch.setattr(auth, "get_user_profiles_bulk", fake_profiles)

        response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {login.token}"},
        )

        assert response.status_code == 200
        data = response.json()
        friend_entry = next(item for item in data if item["user_id"] == str(friend.id))
        assert friend_entry["username"] == f"publicfriend_{unique_id}"
        assert friend_entry["avatar_url"] == f"/api/v1/avatars/users/{friend.id}"


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

    def test_send_friend_request_invalidates_recipient_relationship_cache(
        self, test_client, db_and_modules
    ):
        """Recipient list cache should refresh after a new request is sent."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        sender = auth.register(
            username=f"sendcache_sender_{unique_id}",
            email=f"sendcache_sender_{unique_id}@example.com",
            password="SecurePass123!",
        )
        recipient = auth.register(
            username=f"sendcache_recipient_{unique_id}",
            email=f"sendcache_recipient_{unique_id}@example.com",
            password="SecurePass123!",
        )

        sender_login = auth.login(
            username=f"sendcache_sender_{unique_id}", password="SecurePass123!"
        )
        recipient_login = auth.login(
            username=f"sendcache_recipient_{unique_id}", password="SecurePass123!"
        )

        pre_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {recipient_login.token}"},
        )
        assert pre_response.status_code == 200

        response = test_client.post(
            "/api/v1/relationships",
            headers={"Authorization": f"Bearer {sender_login.token}"},
            json={"user_id": str(recipient.id)},
        )

        assert response.status_code == 200

        post_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {recipient_login.token}"},
        )
        assert post_response.status_code == 200
        data = post_response.json()
        assert any(
            item["user_id"] == str(sender.id)
            and item["status"] == "pending_incoming"
            for item in data
        )


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

    def test_accept_friend_request_invalidates_sender_relationship_cache(
        self, test_client, db_and_modules
    ):
        """Sender list cache should refresh after an incoming request is accepted."""
        auth = db_and_modules["auth"]
        relationships = db_and_modules["relationships"]
        unique_id = uuid.uuid4().hex[:8]

        sender = auth.register(
            username=f"acceptcache_sender_{unique_id}",
            email=f"acceptcache_sender_{unique_id}@example.com",
            password="SecurePass123!",
        )
        recipient = auth.register(
            username=f"acceptcache_recipient_{unique_id}",
            email=f"acceptcache_recipient_{unique_id}@example.com",
            password="SecurePass123!",
        )

        relationships.send_friend_request(sender.id, recipient.id)

        sender_login = auth.login(
            username=f"acceptcache_sender_{unique_id}", password="SecurePass123!"
        )
        recipient_login = auth.login(
            username=f"acceptcache_recipient_{unique_id}", password="SecurePass123!"
        )

        pre_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {sender_login.token}"},
        )
        assert pre_response.status_code == 200
        assert any(
            item["user_id"] == str(recipient.id)
            and item["status"] == "pending_outgoing"
            for item in pre_response.json()
        )

        response = test_client.put(
            f"/api/v1/relationships/{sender.id}/accept",
            headers={"Authorization": f"Bearer {recipient_login.token}"},
        )

        assert response.status_code == 200

        post_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {sender_login.token}"},
        )
        assert post_response.status_code == 200
        data = post_response.json()
        assert any(
            item["user_id"] == str(recipient.id) and item["status"] == "friend"
            for item in data
        )
        assert not any(
            item["user_id"] == str(recipient.id)
            and item["status"] == "pending_outgoing"
            for item in data
        )


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

    def test_block_user_invalidates_relationship_cache(self, test_client, db_and_modules):
        """Blocking should refresh the caller's cached relationship listing."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        blocker = auth.register(
            username=f"blockcache_blocker_{unique_id}",
            email=f"blockcache_blocker_{unique_id}@example.com",
            password="SecurePass123!",
        )
        target = auth.register(
            username=f"blockcache_target_{unique_id}",
            email=f"blockcache_target_{unique_id}@example.com",
            password="SecurePass123!",
        )

        blocker_login = auth.login(
            username=f"blockcache_blocker_{unique_id}", password="SecurePass123!"
        )

        pre_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {blocker_login.token}"},
        )
        assert pre_response.status_code == 200

        response = test_client.post(
            "/api/v1/relationships/block",
            headers={"Authorization": f"Bearer {blocker_login.token}"},
            json={"user_id": str(target.id)},
        )

        assert response.status_code == 200

        post_response = test_client.get(
            "/api/v1/relationships/@me",
            headers={"Authorization": f"Bearer {blocker_login.token}"},
        )
        assert post_response.status_code == 200
        data = post_response.json()
        assert any(
            item["user_id"] == str(target.id) and item["status"] == "blocked"
            for item in data
        )
