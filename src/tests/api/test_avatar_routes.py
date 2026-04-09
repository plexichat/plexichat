"""Tests for avatar routes."""

import src.api.routes.avatars as avatar_routes
import src.core.avatars as avatar_core


class TestGetUserAvatar:
    """Tests for GET /avatars/users/{user_id} endpoint."""

    def test_default_avatar_uses_public_profile_lookup(self, test_client, monkeypatch):
        """Test default avatar generation uses public profile lookup for initials."""
        user_id = 987654321

        class StubAvatars:
            def get_user_avatar_checksum(self, uid):
                assert uid == user_id
                return None

            def get_user_avatar_data(self, uid):
                assert uid == user_id
                return None

        class StubAuth:
            def is_ip_blocked(self, _ip_address):
                return False

            def get_user(self, uid):
                raise AssertionError(
                    "get_user should not be used for default avatar initials"
                )

            def get_user_profiles_bulk(self, user_ids):
                assert user_ids == [user_id]
                return {
                    str(user_id): {
                        "id": user_id,
                        "username": "avatarperson",
                        "created_at": 0,
                        "avatar_url": f"/api/v1/avatars/users/{user_id}",
                        "badges": [],
                    }
                }

        monkeypatch.setattr(avatar_routes.api, "get_avatars", lambda: StubAvatars())
        monkeypatch.setattr(avatar_routes.api, "get_auth", lambda: StubAuth())

        response = test_client.get(f"/api/v1/avatars/users/{user_id}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "AV" in response.text

    def test_default_avatar_uses_username_as_color_seed(self, test_client, monkeypatch):
        """Test default avatar generation uses the username as the seed."""
        user_id = 987654322
        captured = {}

        class StubAvatars:
            def get_user_avatar_checksum(self, uid):
                assert uid == user_id
                return None

            def get_user_avatar_data(self, uid):
                assert uid == user_id
                return None

        class StubAuth:
            def is_ip_blocked(self, _ip_address):
                return False

            def get_user_profiles_bulk(self, user_ids):
                assert user_ids == [user_id]
                return {str(user_id): {"id": user_id, "username": "seedperson"}}

        def fake_generate_default_svg(seed, initials):
            captured["seed"] = seed
            captured["initials"] = initials
            return '<svg xmlns="http://www.w3.org/2000/svg"></svg>'

        monkeypatch.setattr(avatar_routes, "_avatar_checksum_cache", {})
        monkeypatch.setattr(avatar_routes.api, "get_avatars", lambda: StubAvatars())
        monkeypatch.setattr(avatar_routes.api, "get_auth", lambda: StubAuth())
        monkeypatch.setattr(
            avatar_core, "generate_default_svg", fake_generate_default_svg
        )

        response = test_client.get(f"/api/v1/avatars/users/{user_id}")

        assert response.status_code == 200
        assert captured == {"seed": "seedperson", "initials": "SE"}

    def test_default_avatar_falls_back_to_user_id_seed(self, test_client, monkeypatch):
        """Test default avatar generation falls back to the user ID seed."""
        user_id = 987654323
        captured = {}

        class StubAvatars:
            def get_user_avatar_checksum(self, uid):
                assert uid == user_id
                return None

            def get_user_avatar_data(self, uid):
                assert uid == user_id
                return None

        class StubAuth:
            def is_ip_blocked(self, _ip_address):
                return False

            def get_user_profiles_bulk(self, user_ids):
                assert user_ids == [user_id]
                return {}

        def fake_generate_default_svg(seed, initials):
            captured["seed"] = seed
            captured["initials"] = initials
            return '<svg xmlns="http://www.w3.org/2000/svg"></svg>'

        monkeypatch.setattr(avatar_routes, "_avatar_checksum_cache", {})
        monkeypatch.setattr(avatar_routes.api, "get_avatars", lambda: StubAvatars())
        monkeypatch.setattr(avatar_routes.api, "get_auth", lambda: StubAuth())
        monkeypatch.setattr(
            avatar_core, "generate_default_svg", fake_generate_default_svg
        )

        response = test_client.get(f"/api/v1/avatars/users/{user_id}")

        assert response.status_code == 200
        assert captured == {"seed": str(user_id), "initials": "UC"}
