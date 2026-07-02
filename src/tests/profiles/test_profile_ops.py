"""ProfileManager — bio / banner / custom status / bulk read.

All tests route through the manager so the schema (managed by
migrations) is always in lock-step with what the manager expects;
no hand-rolled INSERT statements into ``user_profiles`` so the suite
isn't broken if migrations add/rename columns.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def profiles_setup(db, auth_manager, pii_gen):
    from unittest.mock import patch

    from src.utils import encryption
    from src.core.profiles.manager import ProfileManager

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="profileuser", email=pii_gen.email(), password="TestPass123!"
        )
    # Touch the manager so it creates the default profile row.
    mgr = ProfileManager(db)
    mgr.get_profile(user.id)
    return mgr, user


class TestProfileBasics:
    def test_default_profile_visible(self, profiles_setup):
        mgr, user = profiles_setup
        p = mgr.get_profile(user.id)
        assert p["user_id"] == user.id

    def test_update_profile_bio(self, profiles_setup):
        mgr, user = profiles_setup
        out = mgr.update_profile(user.id, bio="hello world bio")
        assert out["bio"] == "hello world bio"

    def test_set_custom_status_no_expiry(self, profiles_setup):
        mgr, user = profiles_setup
        out = mgr.set_custom_status(
            user_id=user.id, text="doing things", emoji="✨", expires_at=None
        )
        assert "custom_status_text" in out

    def test_set_custom_status_past_expiry_rejected(self, profiles_setup):
        mgr, user = profiles_setup
        with pytest.raises(ValueError):
            mgr.set_custom_status(user_id=user.id, text="too late", expires_at=1)

    def test_clear_custom_status(self, profiles_setup):
        mgr, user = profiles_setup
        mgr.set_custom_status(user_id=user.id, text="x")
        out = mgr.clear_custom_status(user_id=user.id)
        assert out.get("custom_status_text") in (None, "")

    def test_bulk_profiles(self, profiles_setup, auth_manager, pii_gen, db):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.profiles.manager import ProfileManager

        ids = [profiles_setup[1].id]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            for _ in range(2):
                u = auth_manager.register(
                    username=f"profilebulk_{ids[-1]}",
                    email=pii_gen.email(),
                    password="TestPass123!",
                )
                ids.append(u.id)
        mgr = ProfileManager(db)
        bulk = mgr.get_bulk_profiles(ids)
        assert set(bulk.keys()) >= set(ids)
