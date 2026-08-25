"""ProfileManager — bio / banner / custom status / bulk read."""

from __future__ import annotations

import time

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


def _seed(db, uid):
    db.execute(
        "INSERT INTO user_profiles (id, user_id, bio, social_links, created_at, updated_at) "
        f"VALUES ({uid}, {uid}, NULL, '[]', 1000, 1000)"
    )


class TestProfileBugFixes:
    def test_get_profile_is_read_only(self, profiles_setup, db):
        # Bug 1: a read must not create a row.
        p = profiles_setup.get_profile(919191)
        assert p["user_id"] == 919191
        assert (
            db.fetch_one("SELECT * FROM user_profiles WHERE user_id = ?", (919191,))
            is None
        )

    def test_bulk_profiles_is_read_only(self, profiles_setup, db):
        # Bug 1 / 7: bulk read must not insert rows for missing users.
        bulk = profiles_setup.get_bulk_profiles([929292, 939393])
        assert set(bulk.keys()) >= {929292, 939393}
        assert (
            db.fetch_one("SELECT * FROM user_profiles WHERE user_id = ?", (929292,))
            is None
        )

    def test_update_profile_empty_bio_normalised_to_null(self, profiles_setup, db):
        # Bug 4: empty bio stored as NULL, not "".
        _seed(db, 71)
        out = profiles_setup.update_profile(71, bio="")
        assert out["bio"] is None

    def test_update_profile_banner_url_is_stripped(self, profiles_setup, db):
        # Bug 5: whitespace around a valid banner URL must be tolerated.
        _seed(db, 72)
        out = profiles_setup.update_profile(72, banner_url="  https://x.com/b.png  ")
        assert out["banner_url"] == "https://x.com/b.png"

    def test_set_custom_status_partial_keeps_siblings(self, profiles_setup, db):
        # Bug 3: updating only the text must not wipe emoji/expiry.
        _seed(db, 73)
        mgr = profiles_setup
        future_ms = int(time.time() * 1000) + 60000
        mgr.set_custom_status(
            user_id=73, text="hello", emoji="✨", expires_at=future_ms
        )
        mgr.set_custom_status(user_id=73, text="world")
        out = mgr.get_profile(73)
        assert out["custom_status_text"] == "world"
        assert out["custom_status_emoji"] == "✨"
        assert out["custom_status_expires_at"] == future_ms

    def test_set_custom_status_accepts_seconds_expiry(self, profiles_setup, db):
        # Bug 6: epoch seconds must be accepted (and converted to ms).
        _seed(db, 74)
        future_sec = int(time.time()) + 60
        out = profiles_setup.set_custom_status(
            user_id=74, text="x", expires_at=future_sec
        )
        assert out["custom_status_expires_at"] == future_sec * 1000

    def test_set_custom_status_past_seconds_rejected(self, profiles_setup, db):
        # Bug 6: past epoch seconds must still be rejected.
        _seed(db, 75)
        with pytest.raises(ValueError):
            profiles_setup.set_custom_status(user_id=75, text="x", expires_at=1)

    def test_reap_expired_custom_statuses(self, profiles_setup, db):
        # Bug 2: expired statuses must actually be cleared from the DB.
        past = int(time.time() * 1000) - 10000
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, custom_status_text, "
            "custom_status_emoji, custom_status_expires_at, created_at, updated_at) "
            "VALUES (76, 76, NULL, '[]', 'stale', 'e', ?, 1000, 1000)",
            (past,),
        )
        n = profiles_setup.reap_expired_custom_statuses()
        assert n >= 1
        row = db.fetch_one(
            "SELECT custom_status_text, custom_status_emoji, custom_status_expires_at "
            "FROM user_profiles WHERE user_id = 76"
        )
        assert row["custom_status_text"] is None
        assert row["custom_status_emoji"] is None
        assert row["custom_status_expires_at"] is None

    def test_validate_social_links_keeps_path_javascript(self, profiles_setup):
        # Bug 8: a legitimate URL whose path contains "javascript" is kept, a
        # scheme-less "javascript:..." is normalised to https:// (non-executable)
        # rather than dropped, and no returned URL may carry a dangerous scheme.
        out = profiles_setup._validate_social_links(
            [
                {"platform": "github", "url": "https://example.com/x?q=javascript:1"},
                {"platform": "other", "url": "javascript:alert(1)"},
                "not-a-dict",
            ]
        )
        assert len(out) == 2
        assert out[0]["url"] == "https://example.com/x?q=javascript:1"
        assert out[1]["url"] == "https://javascript:alert(1)"
        assert all(
            not u["url"].lower().startswith(("javascript:", "data:")) for u in out
        )

    def test_validate_social_links_skips_nondict(self, profiles_setup):
        # Bug 8: malformed (non-dict) entries are skipped, not 500'd.
        out = profiles_setup._validate_social_links(["oops", None, 123])
        assert out == []


class TestProfileClearingAndHygiene:
    def test_empty_string_clears_fields(self, profiles_setup, db):
        # F3: "" must CLEAR; omitted/None must leave unchanged.
        _seed(db, 81)
        mgr = profiles_setup
        mgr.update_profile(
            81,
            bio="original",
            pronouns="they/them",
            location="Earth",
            timezone="UTC",
            banner_url="https://x.com/b.png",
        )
        # None leaves unchanged
        out = mgr.update_profile(81)
        assert out["bio"] == "original"
        # Empty strings clear
        out = mgr.update_profile(81, bio="", pronouns="", location="", timezone="")
        assert out["bio"] is None
        assert out["pronouns"] is None
        assert out["location"] is None
        assert out["timezone"] is None

    def test_empty_social_links_list_clears(self, profiles_setup, db):
        _seed(db, 82)
        mgr = profiles_setup
        mgr.update_profile(
            82, social_links=[{"platform": "github", "url": "https://github.com/x"}]
        )
        out = mgr.update_profile(82, social_links=[])
        assert out["social_links"] == []

    def test_banner_path_containing_dangerous_substring_is_kept(
        self, profiles_setup, db
    ):
        # F5: prefix checks only — a path containing "database:" is legit.
        _seed(db, 83)
        out = profiles_setup.update_profile(
            83, banner_url="https://cdn.example.com/database:diagram.png"
        )
        assert out["banner_url"] == "https://cdn.example.com/database:diagram.png"

    def test_banner_javascript_scheme_rejected(self, profiles_setup, db):
        _seed(db, 84)
        with pytest.raises(ValueError):
            profiles_setup.update_profile(84, banner_url="javascript:alert(1)")

    def test_expired_flag_not_exposed_by_reads(self, profiles_setup, db):
        # F7: internal _custom_status_expired key never crosses the boundary.
        past = int(time.time() * 1000) - 10000
        db.execute(
            "INSERT INTO user_profiles (id, user_id, bio, social_links, custom_status_text, "
            "custom_status_emoji, custom_status_expires_at, created_at, updated_at) "
            "VALUES (85, 85, NULL, '[]', 'stale', 'e', ?, 1000, 1000)",
            (past,),
        )
        p = profiles_setup.get_profile(85)
        assert "_custom_status_expired" not in p
        bulk = profiles_setup.get_bulk_profiles([85])
        assert "_custom_status_expired" not in bulk[85]
        out = profiles_setup.set_custom_status(user_id=85, text="fresh")
        assert "_custom_status_expired" not in out

    def test_custom_status_dual_write_consistent(self, profiles_setup, db):
        # F8: user_profiles and auth_users stay in sync after set.
        db.execute(
            "INSERT INTO auth_users (id, username, password_hash, permissions, created_at, updated_at) "
            "VALUES (86, 'statususer86', 'x', '{}', 1000, 1000)"
        )
        _seed(db, 86)
        future_ms = int(time.time() * 1000) + 60000
        profiles_setup.set_custom_status(
            user_id=86, text="sync", emoji="✨", expires_at=future_ms
        )
        prof = db.fetch_one(
            "SELECT custom_status_text, custom_status_emoji FROM user_profiles "
            "WHERE user_id = 86"
        )
        auth_row = db.fetch_one(
            "SELECT custom_status_text, custom_status_emoji FROM auth_users WHERE id = 86"
        )
        assert prof["custom_status_text"] == auth_row["custom_status_text"] == "sync"
        assert prof["custom_status_emoji"] == auth_row["custom_status_emoji"] == "✨"
