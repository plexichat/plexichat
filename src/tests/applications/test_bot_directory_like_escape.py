"""Regression: bot-directory text filters must treat LIKE metacharacters literally.

``get_bot_directory`` builds ``LIKE ?`` clauses from the caller-supplied ``q``
and ``tag`` filters (HTTP query params on ``GET /bots/directory``). Without an
escape, a bare ``%`` / ``_`` acts as a wildcard instead of a literal character,
so a search like ``?q=%`` silently matched every bot in the directory and a
crafted ``?tag=%`` widened the tag filter. These tests pin the fix: the escape
character is declared, metacharacters are treated literally, and ordinary
substring matches still work.
"""

import json

import pytest

from src.core.applications.manager.bot_management import (
    invalidate_bot_directory_cache,
)


@pytest.fixture(autouse=True)
def _clear_directory_cache():
    invalidate_bot_directory_cache()
    yield
    invalidate_bot_directory_cache()


def _seed(app_manager, test_user, specs):
    """Insert visible bots straight into storage (bypasses write-side limits)."""
    db = app_manager._db
    bot_rows, app_rows, profile_rows = [], [], []
    for idx, (name, description, tags) in enumerate(specs):
        aid = 70_000_000 + idx
        bot_rows.append((aid, test_user.id, f"probe_{idx}", name, "hash", "{}", 1))
        app_rows.append(
            (
                aid,
                test_user.id,
                name,
                description,
                None,
                aid,
                1,
                0,
                "[]",
                "[]",
                None,
                "secret",
                1,
                1,
            )
        )
        profile_rows.append(
            (
                aid,
                description,
                description,
                None,
                None,
                None,
                None,
                None,
                json.dumps(tags),
                0,
                0,
                1,
            )
        )
    db.execute_many(
        """INSERT INTO auth_bots
           (id, owner_id, username, display_name, token_hash, permissions, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        bot_rows,
    )
    db.execute_many(
        """INSERT INTO app_applications
           (id, owner_id, name, description, icon_url, bot_id, bot_public,
            bot_require_code_grant, redirect_uris, allowed_scopes,
            interactions_endpoint_url, client_secret_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        app_rows,
    )
    db.execute_many(
        """INSERT INTO app_bot_profiles
           (application_id, description, short_description, avatar_url, banner_url,
            website_url, support_url, github_url, tags, nsfw, private, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        profile_rows,
    )


@pytest.mark.applications
class TestBotDirectoryLikeEscaping:
    def test_percent_query_does_not_match_every_bot(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        result = app_manager.get_bot_directory(include_public=True, q="%")
        assert result["total"] == 0
        assert result["bot_list"] == []

    def test_underscore_query_is_literal(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        assert app_manager.get_bot_directory(include_public=True, q="_")["total"] == 0

    def test_backslash_query_is_literal(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        assert app_manager.get_bot_directory(include_public=True, q="\\")["total"] == 0

    def test_ordinary_query_still_matches(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        result = app_manager.get_bot_directory(include_public=True, q="Alpha")
        assert result["total"] == 1
        assert result["bot_list"][0]["name"] == "AlphaBot"

    def test_percent_tag_does_not_match_every_bot(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        assert app_manager.get_bot_directory(include_public=True, tag="%")["total"] == 0

    def test_ordinary_tag_still_matches(self, app_manager, test_user):
        _seed(app_manager, test_user, [("AlphaBot", "alpha helper", ["utility"])])
        result = app_manager.get_bot_directory(include_public=True, tag="utility")
        assert result["total"] == 1
        assert result["bot_list"][0]["name"] == "AlphaBot"
