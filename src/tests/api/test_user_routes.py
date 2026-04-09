"""
Tests for user routes.
"""

import uuid


class TestGetCurrentUser:
    """Tests for GET /users/@me endpoint."""

    def test_get_current_user_success(self, test_client, auth_headers, test_user):
        """Test getting current user info."""
        response = test_client.get("/api/v1/users/@me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == test_user["username"]
        assert "id" in data
        assert "created_at" in data

    def test_get_current_user_without_auth(self, test_client):
        """Test getting current user without authentication."""
        response = test_client.get("/api/v1/users/@me")

        assert response.status_code == 401

    def test_get_current_user_includes_email(self, test_client, auth_headers):
        """Test that current user response includes email."""
        response = test_client.get("/api/v1/users/@me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "email" in data


class TestUpdateCurrentUser:
    """Tests for PATCH /users/@me endpoint."""

    def test_update_username(self, test_client, db_and_modules):
        """Test updating username."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        auth.register(
            username=f"updateuser_{unique_id}",
            email=f"updateuser_{unique_id}@example.com",
            password="SecurePass123!",
        )

        result = auth.login(
            username=f"updateuser_{unique_id}", password="SecurePass123!"
        )

        new_username = f"updated_{unique_id}"
        response = test_client.patch(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {result.token}"},
            json={"username": new_username},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == new_username

    def test_update_without_auth(self, test_client):
        """Test updating user without authentication."""
        response = test_client.patch("/api/v1/users/@me", json={"username": "newname"})

        assert response.status_code == 401


class TestGetUser:
    """Tests for GET /users/{user_id} endpoint."""

    def test_get_user_by_id(self, test_client, auth_headers, test_user):
        """Test getting user by ID."""
        user_id = str(test_user["user"].id)

        response = test_client.get(f"/api/v1/users/{user_id}", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == user_id
        assert data["username"] == test_user["username"]

    def test_get_user_public_fields_only(self, test_client, auth_headers, test_user):
        """Test that public user response excludes private fields."""
        user_id = str(test_user["user"].id)

        response = test_client.get(f"/api/v1/users/{user_id}", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "email" not in data or data["email"] is None

    def test_get_user_by_id_uses_public_profile_lookup(
        self, test_client, auth_headers, db_and_modules, monkeypatch
    ):
        """Test public user route uses the public profile helper."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]
        target = auth.register(
            username=f"publictarget_{unique_id}",
            email=f"publictarget_{unique_id}@example.com",
            password="SecurePass123!",
        )

        def fail_get_user(*args, **kwargs):
            raise AssertionError("get_user should not be used for public user route")

        def fake_profiles(user_ids):
            assert user_ids == [target.id]
            return {
                str(target.id): {
                    "id": target.id,
                    "username": f"public_{unique_id}",
                    "created_at": target.created_at,
                    "avatar_url": f"/api/v1/avatars/users/{target.id}",
                    "badges": [],
                }
            }

        monkeypatch.setattr(auth, "get_user", fail_get_user)
        monkeypatch.setattr(auth, "get_user_profiles_bulk", fake_profiles)

        response = test_client.get(
            f"/api/v1/users/{target.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(target.id)
        assert data["username"] == f"public_{unique_id}"
        assert data["created_at"] == target.created_at

    def test_get_nonexistent_user(self, test_client, auth_headers):
        """Test getting nonexistent user."""
        response = test_client.get(
            "/api/v1/users/999999999999999999", headers=auth_headers
        )

        assert response.status_code == 404

    def test_get_user_invalid_id(self, test_client, auth_headers):
        """Test getting user with invalid ID."""
        response = test_client.get("/api/v1/users/invalid_id", headers=auth_headers)

        assert response.status_code == 400

    def test_get_user_without_auth(self, test_client, test_user):
        """Test getting user without authentication."""
        user_id = str(test_user["user"].id)

        response = test_client.get(f"/api/v1/users/{user_id}")

        assert response.status_code == 401
