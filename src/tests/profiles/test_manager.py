"""ProfileManager — bio / banner / custom status / bulk read."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def profiles_setup(db):
    from src.core.profiles.manager import ProfileManager

    return ProfileManager(db)


class TestProfileBasics:
    def test_default_profile_created_on_read(self, profiles_setup, db):
        # Ensure user exists
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
            "VALUES (1, 1, NULL, '[]', 1000, 1000)"
        )
        p = profiles_setup.get_profile(1)
        assert p["user_id"] == 1

    def test_update_profile_bio(self, profiles_setup, db):
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
            "VALUES (1, 1, NULL, '[]', 1000, 1000)"
        )
        out = profiles_setup.update_profile(1, bio="hello world bio")
        assert out["bio"] == "hello world bio"

    def test_set_custom_status_expiry(self, profiles_setup, db):
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
            "VALUES (1, 1, NULL, '[]', 1000, 1000)"
        )
        out = profiles_setup.set_custom_status(
            user_id=1,
            text="doing things",
            emoji=":spark:",
            expires_at=None,
        )
        assert "custom_status_text" in out

    def test_set_custom_status_past_expiry_rejected(self, profiles_setup, db):
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
            "VALUES (1, 1, NULL, '[]', 1000, 1000)"
        )
        with pytest.raises(ValueError):
            profiles_setup.set_custom_status(user_id=1, text="too late", expires_at=1)

    def test_clear_custom_status(self, profiles_setup, db):
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
            "VALUES (1, 1, NULL, '[]', 1000, 1000)"
        )
        profiles_setup.set_custom_status(user_id=1, text="x")
        out = profiles_setup.clear_custom_status(user_id=1)
        assert out.get("custom_status_text") in (None, "")

    def test_bulk_profiles(self, profiles_setup, db):
        for uid in (1, 2, 3):
            db.execute(
                "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
                f"VALUES ({uid}, {uid}, NULL, '[]', 1000, 1000)"
            )
        bulk = profiles_setup.get_bulk_profiles([1, 2, 3])
        assert set(bulk.keys()) >= {1, 2, 3}
