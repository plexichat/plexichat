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


@pytest.mark.applications
class TestBotPermissionLookups:
    def test_review_does_not_repeat_permission_check(
        self, app_manager, server_manager, user_pool
    ):
        """Reviewing a request checks server.manage once, not twice.

        ``review_bot_request`` authorizes the reviewer and then auto-approves;
        the auto-approval must reuse that decision instead of resolving the
        same permission again.
        """
        reviewer = user_pool.get_user()
        requester = user_pool.get_user()
        server = server_manager.create_server(reviewer.id, "Bot Server")
        app_manager._servers = server_manager
        app = app_manager.create_application(reviewer.id, "Bot App")
        request = app_manager.request_bot(server.id, app.id, requester.id)

        real_check = app_manager._require_server_manage_permission
        with patch(
            "utils.licensing.has_feature", return_value=True
        ), patch.object(
            app_manager,
            "_require_server_manage_permission",
            side_effect=real_check,
        ) as permission_check:
            app_manager.review_bot_request(
                server_id=server.id,
                request_id=request.id,
                reviewer_id=reviewer.id,
                approve=True,
            )

        assert permission_check.call_count == 1

    def test_direct_approval_still_checks_permission(self, app_manager, user_pool):
        """Skipping the duplicate check must not weaken direct approvals."""
        from src.core.applications.exceptions import PermissionDeniedError

        user = user_pool.get_user()
        app = app_manager.create_application(user.id, "Bot App")
        with pytest.raises(PermissionDeniedError):
            app_manager.approve_bot(server_id=7002, application_id=app.id, approved_by=user.id)


@pytest.mark.applications
class TestBotProfileReadPath:
    def test_public_profile_avoids_application_join(self, app_manager, user_pool):
        """A public profile read needs only the profile row."""
        user = user_pool.get_user()
        app = app_manager.create_application(user.id, "Profile App")
        app_manager.update_bot_profile(app.id, user.id, description="public profile")

        real_fetch_one = app_manager._db.fetch_one
        queries = []

        def tracking_fetch_one(query, params=None):
            queries.append(query)
            return real_fetch_one(query, params)

        with patch.object(app_manager._db, "fetch_one", side_effect=tracking_fetch_one):
            profile = app_manager.get_bot_profile(app.id)

        assert profile is not None and profile.description == "public profile"
        assert len(queries) == 1
        assert "JOIN" not in queries[0]
        assert "app_applications" not in queries[0]

    def test_private_profile_still_hides_from_non_owners(self, app_manager, user_pool):
        """The lazy owner lookup keeps the private-profile gate intact."""
        owner = user_pool.get_user()
        other = user_pool.get_user()
        app = app_manager.create_application(owner.id, "Private Profile App")
        app_manager.update_bot_profile(app.id, owner.id, private=True)

        assert app_manager.get_bot_profile(app.id) is None
        assert app_manager.get_bot_profile(app.id, viewer_id=other.id) is None
        assert app_manager.get_bot_profile(app.id, viewer_id=owner.id) is not None


@pytest.mark.applications
class TestApplicationListMapping:
    def test_list_can_skip_redirect_uri_decoding(self, app_manager, test_user):
        """The list path can omit redirect URIs while detail reads keep them."""
        app_manager.create_application(
            owner_id=test_user.id,
            name="List App",
            redirect_uris=["https://example.com/cb"],
        )

        full = app_manager.get_user_applications(test_user.id)[0]
        assert full.redirect_uris == ["https://example.com/cb"]

        summary = app_manager.get_user_applications(
            test_user.id, parse_redirect_uris=False
        )[0]
        assert summary.name == "List App"
        assert summary.redirect_uris == []
