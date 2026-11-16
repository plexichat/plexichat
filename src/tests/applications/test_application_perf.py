"""Performance-oriented regression tests for the applications core.

These assert the *shape* of the read paths (query counts and cached slices)
rather than wall-clock time, so they are deterministic.
"""

import pytest
from unittest.mock import patch

from src.core.applications.manager.bot_management import (
    invalidate_bot_directory_cache,
)


@pytest.fixture(autouse=True)
def _clear_directory_cache():
    """The directory cache is module-global; keep tests isolated."""
    invalidate_bot_directory_cache()
    yield
    invalidate_bot_directory_cache()


@pytest.mark.applications
class TestBotDirectoryCache:
    def test_repeated_directory_read_skips_sql(self, app_manager):
        """A warm directory slice avoids both the COUNT and the page query."""
        from src.core.applications.manager import bot_management as bm

        real_fetch_all = app_manager._db.fetch_all
        real_fetch_one = app_manager._db.fetch_one
        calls = []

        def tracking_fetch_all(query, params=None):
            calls.append(query)
            return real_fetch_all(query, params)

        def tracking_fetch_one(query, params=None):
            calls.append(query)
            return real_fetch_one(query, params)

        with (
            patch.object(app_manager._db, "fetch_all", side_effect=tracking_fetch_all),
            patch.object(app_manager._db, "fetch_one", side_effect=tracking_fetch_one),
        ):
            first = app_manager.get_bot_directory()
            calls.clear()
            second = app_manager.get_bot_directory()

        assert len(calls) == 0  # warm slice: no COUNT and no page query
        assert second == first
        assert bm._directory_cache  # slice was actually cached

    def test_write_invalidation_makes_new_bot_visible(
        self, app_manager, auth_manager, test_user
    ):
        """A directory-visible write invalidates the cached slice."""
        db = app_manager._db
        assert app_manager.get_bot_directory()["total"] == 0

        app = app_manager.create_application(owner_id=test_user.id, name="Cached Bot")
        bot = auth_manager.create_bot(
            owner_id=test_user.id,
            username="cached_bot_perf",
            display_name="Cached Bot",
        )
        db.execute(
            "UPDATE app_applications SET bot_id = ? WHERE id = ?",
            (bot.id, app.id),
        )
        db.execute(
            """INSERT INTO app_bot_profiles
               (application_id, tags, nsfw, private, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (app.id, '["utility"]', 0, 0, 1),
        )

        # Without invalidation the stale empty slice would be returned.
        assert app_manager.get_bot_directory()["total"] == 0
        invalidate_bot_directory_cache()
        result = app_manager.get_bot_directory()
        assert result["total"] == 1
        assert result["bot_list"][0]["name"] == "Cached Bot"

    def test_cached_slice_is_not_aliased_to_callers(self, app_manager):
        """Mutating a returned slice must not corrupt the cached value."""
        first = app_manager.get_bot_directory()
        first["bot_list"].append({"id": 999})
        first["total"] = -1
        assert app_manager.get_bot_directory() == {"bot_list": [], "total": 0}


@pytest.mark.applications
class TestApprovedBotListing:
    def test_listing_does_not_join_unused_application_columns(
        self, app_manager, test_user
    ):
        """Approved-bot listings read only the columns callers consume."""
        app = app_manager.create_application(owner_id=test_user.id, name="Approved Bot")
        app_manager._db.execute(
            """INSERT INTO app_approved_bots
               (id, server_id, application_id, approved_by, permissions,
                bot_name, bot_avatar_url, status, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (9001, 7001, app.id, test_user.id, "{}", "Approved", None, "approved", 1, 1),
        )

        real_fetch_all = app_manager._db.fetch_all
        queries = []

        def tracking_fetch_all(query, params=None):
            queries.append(query)
            return real_fetch_all(query, params)

        with patch.object(app_manager._db, "fetch_all", side_effect=tracking_fetch_all):
            bots = app_manager.get_approved_bots(server_id=7001, status="approved")

        assert len(bots) == 1
        assert bots[0].bot_name == "Approved"
        assert "LEFT JOIN" not in queries[0]
        assert "app_applications" not in queries[0]
