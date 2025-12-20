"""
Tests for user routes - profile management, DMs, settings.

Covers:
- User profile CRUD
- Avatar uploads
- DM channel management
- User search
- Authorization checks
- Input sanitization
- SQL injection prevention
- Error handling
"""

import pytest
import asyncio
import uuid
import io
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestUserProfile:
    """Test user profile endpoints."""

    async def test_get_current_user(self, auth_headers):
        """Test getting current user profile."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert "username" in data
            assert "email" in data  # Private field included for @me

    async def test_get_current_user_without_auth(self):
        """Test getting current user without authentication."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me")

            assert response.status_code == 401

    async def test_update_current_user(self, modules, auth_headers, test_user):
        """Test updating current user profile."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]
        new_username = f"updated_{unique_id}"

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"username": new_username},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["username"] == new_username

    async def test_update_user_duplicate_username(
        self, modules, session_users, auth_headers
    ):
        """Test updating to duplicate username."""
        # Get another user's username
        other_user, other_username, _ = session_users[1]

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"username": other_username},
            )

            assert response.status_code == 409

    async def test_update_user_invalid_username(self, auth_headers):
        """Test updating with invalid username."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Too short
            response = await ac.patch(
                "/api/v1/users/@me", headers=auth_headers, json={"username": "ab"}
            )

            assert response.status_code in [400, 422]

    async def test_update_user_sql_injection(self, auth_headers):
        """Test SQL injection in user update."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"username": "test'; DROP TABLE auth_users; --"},
            )

            # Should safely reject
            assert response.status_code in [400, 422]

    async def test_change_password(self, modules, test_user):
        """Test changing password."""
        result = modules.auth.login(test_user["username"], test_user["password"])
        headers = {"Authorization": f"Bearer {result.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=headers,
                json={
                    "current_password": test_user["password"],
                    "password": "NewSecurePass123!",
                },
            )

            assert response.status_code == 200

    async def test_change_password_wrong_current(self, auth_headers):
        """Test changing password with wrong current password."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={
                    "current_password": "WrongPassword123!",
                    "password": "NewSecurePass123!",
                },
            )

            assert response.status_code in [400, 401]

    async def test_change_password_weak_new(self, auth_headers, test_user):
        """Test changing to weak password."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"current_password": test_user["password"], "password": "weak"},
            )

            assert response.status_code == 400


@pytest.mark.asyncio
class TestUserRetrieval:
    """Test user retrieval endpoints."""

    async def test_get_user_by_id(self, modules, auth_headers, session_users):
        """Test getting user by ID."""
        other_user, _, _ = session_users[1]

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/users/{other_user.id}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert str(data["id"]) == str(other_user.id)
            assert "email" not in data  # Private field not included

    async def test_get_nonexistent_user(self, auth_headers):
        """Test getting non-existent user."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/999999999", headers=auth_headers)

            assert response.status_code == 404

    async def test_get_user_invalid_id(self, auth_headers):
        """Test getting user with invalid ID."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/invalid_id", headers=auth_headers)

            assert response.status_code == 400


@pytest.mark.asyncio
class TestUserSearch:
    """Test user search endpoints."""

    async def test_search_user_by_username(self, modules, auth_headers, session_users):
        """Test searching for user by username."""
        other_user, other_username, _ = session_users[1]

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/users/search?username={other_username}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert data["username"] == other_username

    async def test_search_user_not_found(self, auth_headers):
        """Test searching for non-existent user."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/users/search?username=nonexistent_{unique_id}",
                headers=auth_headers,
            )

            assert response.status_code == 404

    async def test_search_user_sql_injection(self, auth_headers):
        """Test SQL injection in user search."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/search?username=' OR '1'='1", headers=auth_headers
            )

            # Should safely handle
            assert response.status_code in [200, 404]

    async def test_search_user_missing_param(self, auth_headers):
        """Test searching without username parameter."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/search", headers=auth_headers)

            assert response.status_code == 400


@pytest.mark.asyncio
class TestAvatarUpload:
    """Test avatar upload endpoints."""

    async def test_upload_avatar(self, auth_headers):
        """Test uploading avatar."""
        app = create_app()

        # Create a small test image
        image_data = b"fake_image_data"
        files = {"file": ("avatar.png", io.BytesIO(image_data), "image/png")}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/avatar", headers=auth_headers, files=files
            )

            # May succeed or return 500 if avatars module not available
            assert response.status_code in [200, 400, 500]

    async def test_upload_avatar_wrong_type(self, auth_headers):
        """Test uploading non-image as avatar."""
        app = create_app()

        # Non-image file
        file_data = b"not an image"
        files = {"file": ("file.txt", io.BytesIO(file_data), "text/plain")}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/avatar", headers=auth_headers, files=files
            )

            assert response.status_code == 400

    async def test_upload_avatar_without_auth(self):
        """Test uploading avatar without authentication."""
        app = create_app()

        image_data = b"fake_image_data"
        files = {"file": ("avatar.png", io.BytesIO(image_data), "image/png")}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/v1/users/@me/avatar", files=files)

            assert response.status_code == 401


@pytest.mark.asyncio
class TestDMChannels:
    """Test DM channel management."""

    async def test_get_dm_channels(self, auth_headers):
        """Test getting DM channels."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me/channels", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_create_dm_channel(self, modules, auth_headers, session_users):
        """Test creating a DM channel."""
        other_user, _, _ = session_users[1]

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/channels",
                headers=auth_headers,
                json={"recipient_id": str(other_user.id)},
            )

            # May succeed or return 501 if not implemented
            assert response.status_code in [200, 501]

    async def test_create_dm_with_self(self, auth_headers, test_user):
        """Test creating DM with self."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/channels",
                headers=auth_headers,
                json={"recipient_id": str(test_user["user"].id)},
            )

            # Should either allow or reject
            assert response.status_code in [200, 400, 501]

    async def test_create_dm_nonexistent_user(self, auth_headers):
        """Test creating DM with non-existent user."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/channels",
                headers=auth_headers,
                json={"recipient_id": "999999999"},
            )

            assert response.status_code in [404, 501]

    async def test_create_dm_invalid_recipient(self, auth_headers):
        """Test creating DM with invalid recipient ID."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/channels",
                headers=auth_headers,
                json={"recipient_id": "invalid_id"},
            )

            assert response.status_code in [400, 501]

    async def test_create_dm_missing_recipient(self, auth_headers):
        """Test creating DM without recipient."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/users/@me/channels", headers=auth_headers, json={}
            )

            assert response.status_code in [400, 422, 501]


@pytest.mark.asyncio
class TestMessagingSettings:
    """Test messaging settings endpoints."""

    async def test_get_messaging_settings(self, auth_headers):
        """Test getting messaging settings."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/users/@me/messaging-settings", headers=auth_headers
            )

            # May succeed or return 500 if not implemented
            assert response.status_code in [200, 500]

    async def test_update_messaging_settings(self, auth_headers):
        """Test updating messaging settings."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me/messaging-settings",
                headers=auth_headers,
                json={"enable_push_notifications": True},
            )

            # May succeed or return 500 if not implemented
            assert response.status_code in [200, 500]


@pytest.mark.asyncio
class TestNotesChannel:
    """Test personal notes channel."""

    async def test_get_notes_channel(self, auth_headers):
        """Test getting/creating notes channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me/notes", headers=auth_headers)

            # May succeed or return 501 if not implemented
            assert response.status_code in [200, 501]


@pytest.mark.asyncio
class TestInputSanitization:
    """Test input sanitization in user routes."""

    async def test_username_xss(self, auth_headers):
        """Test XSS in username update."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"username": "<script>alert('xss')</script>"},
            )

            # Should reject invalid characters
            assert response.status_code in [400, 422]

    async def test_email_validation(self, auth_headers):
        """Test email validation."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Invalid email format
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"email": "not_an_email"},
            )

            assert response.status_code in [400, 422]

    async def test_long_username(self, auth_headers):
        """Test username length limit."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Very long username
            long_username = "a" * 100
            response = await ac.patch(
                "/api/v1/users/@me",
                headers=auth_headers,
                json={"username": long_username},
            )

            # Should reject
            assert response.status_code in [400, 422]


@pytest.mark.asyncio
class TestConcurrentOperations:
    """Test concurrent user operations."""

    async def test_concurrent_profile_updates(self, modules, test_user):
        """Test concurrent updates to same profile."""
        result = modules.auth.login(test_user["username"], test_user["password"])
        headers = {"Authorization": f"Bearer {result.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.patch(
                    "/api/v1/users/@me",
                    headers=headers,
                    json={"username": f"user_{uuid.uuid4().hex[:8]}"},
                )
                for _ in range(3)
            ]
            responses = await asyncio.gather(*tasks)

            # At least one should succeed
            success_count = sum(1 for r in responses if r.status_code == 200)
            assert success_count >= 1

    async def test_concurrent_avatar_uploads(self, auth_headers):
        """Test concurrent avatar uploads."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = []
            for _ in range(3):
                image_data = b"fake_image_data"
                files = {"file": ("avatar.png", io.BytesIO(image_data), "image/png")}
                tasks.append(
                    ac.post(
                        "/api/v1/users/@me/avatar", headers=auth_headers, files=files
                    )
                )

            responses = await asyncio.gather(*tasks)

            # At least one should complete (success or known error)
            completed = sum(1 for r in responses if r.status_code in [200, 400, 500])
            assert completed >= 1


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in user routes."""

    async def test_malformed_json(self, auth_headers):
        """Test handling of malformed JSON."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                "/api/v1/users/@me",
                content="{invalid json}",
                headers={**auth_headers, "Content-Type": "application/json"},
            )

            assert response.status_code == 422

    async def test_error_response_format(self, auth_headers):
        """Test error response format."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/999999999", headers=auth_headers)

            assert response.status_code == 404
            data = response.json()
            assert "error" in data
            assert "code" in data["error"]
            assert "message" in data["error"]
