"""Regression tests for the public bot directory read path.

Covers three properties of ``GET /bots/directory`` and
``GET /bots/authorized-apps`` after the performance work:

* identical output whether the directory slice is cold or cached;
* the read shape stays bounded and cache-backed (no unbounded page, no SQL
  round trip on a warm read);
* bot-authored metadata can never be served as executable markup, even when
  the stored row predates the write-side text policy.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

import src.api.routes.bots as bots_route
from src.api.routes.bots import BotDirectoryEntry
from src.core.applications.manager.bot_management import (
    MAX_DIRECTORY_PAGE_SIZE,
    invalidate_bot_directory_cache,
)


@pytest.fixture(autouse=True)
def _clear_directory_cache():
    invalidate_bot_directory_cache()
    yield
    invalidate_bot_directory_cache()


def _seed_bots(app_manager, test_user, specs):
    """Insert bot applications/profiles straight into storage (bypasses limit)."""
    db = app_manager._db
    bot_rows, app_rows, profile_rows = [], [], []
    for idx, (name, description, tags) in enumerate(specs):
        aid = 50_000_000 + idx
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


class _Tok:
    def __init__(self, user_id):
        self.user_id = user_id


def _call_directory(**overrides):
    params = dict(server_id=None, q=None, tag=None, limit=50, offset=0)
    params.update(overrides)
    return asyncio.run(bots_route.list_bot_directory(**params))


@pytest.mark.applications
class TestDirectoryReadShape:
    def test_cold_and_warm_reads_are_identical(self, app_manager, test_user):
        _seed_bots(
            app_manager,
            test_user,
            [("Clean Bot", "A helpful bot", ["utility", "moderation"])],
        )
        with patch.object(
            bots_route.applications,
            "get_bot_directory",
            side_effect=lambda **kw: app_manager.get_bot_directory(**kw),
        ):
            invalidate_bot_directory_cache()
            cold = _call_directory()
            warm = _call_directory()
        assert cold == warm
        assert cold.total == 1
        assert cold.bots[0].name == "Clean Bot"
        assert cold.bots[0].tags == ["utility", "moderation"]

    def test_warm_read_skips_sql(self, app_manager, test_user):
        """The cached slice avoids both the COUNT and the page query."""
        _seed_bots(app_manager, test_user, [("Cached Bot", "desc", [])])
        real_fetch_all = app_manager._db.fetch_all
        real_fetch_one = app_manager._db.fetch_one
        calls = []

        def fa(query, params=None):
            calls.append(query)
            return real_fetch_all(query, params)

        def fo(query, params=None):
            calls.append(query)
            return real_fetch_one(query, params)

        with (
            patch.object(
                bots_route.applications,
                "get_bot_directory",
                side_effect=lambda **kw: app_manager.get_bot_directory(**kw),
            ),
            patch.object(app_manager._db, "fetch_all", side_effect=fa),
            patch.object(app_manager._db, "fetch_one", side_effect=fo),
        ):
            invalidate_bot_directory_cache()
            _call_directory()  # cold: populates the slice
            calls.clear()
            _call_directory()  # warm: must not touch the DB
        assert calls == []

    def test_page_size_and_offset_are_bounded(self, app_manager, test_user):
        """The HTTP clamp and the manager cap agree on a hard page ceiling."""
        _seed_bots(app_manager, test_user, [("Bounded Bot", "desc", [])])
        with patch.object(
            bots_route.applications,
            "get_bot_directory",
            side_effect=lambda **kw: app_manager.get_bot_directory(**kw),
        ):
            # Query allows up to 200; the route clamps to the manager ceiling.
            result = _call_directory(limit=200, offset=0)
        assert result.limit == MAX_DIRECTORY_PAGE_SIZE == 100
        assert result.offset == 0


@pytest.mark.applications
class TestDirectoryMetadataIsNeutralized:
    @pytest.mark.parametrize(
        "payload",
        [
            '<img src=x onerror="alert(1)">',
            "<script>alert(1)</script>",
            "<svg/onload=alert(1)>",
        ],
    )
    def test_markup_is_stripped_from_entry_text(self, payload):
        """Even a stored payload can never reach the response as a tag."""
        entry = BotDirectoryEntry(
            id=1,
            name=f"Bot {payload}",
            description=payload,
            tags=[payload, "clean"],
            bot_id="2",
        )
        for rendered in (entry.name, entry.description, *entry.tags):
            assert "<" not in rendered and ">" not in rendered
        assert "clean" in entry.tags
        assert entry.tags[-1] == "clean"

    def test_route_serves_neutralized_metadata(self, app_manager, test_user):
        _seed_bots(
            app_manager,
            test_user,
            [("XSS Bot", '<img src=x onerror="alert(1)">', ["<b>bold</b>", "safe"])],
        )
        with patch.object(
            bots_route.applications,
            "get_bot_directory",
            side_effect=lambda **kw: app_manager.get_bot_directory(**kw),
        ):
            result = _call_directory()
        bot = result.bots[0]
        assert "<" not in bot.name and ">" not in bot.name
        assert "<" not in (bot.description or "") and ">" not in (bot.description or "")
        assert all("<" not in tag and ">" not in tag for tag in bot.tags)
        assert "safe" in bot.tags


@pytest.mark.applications
class TestAuthorizedAppsOutput:
    def test_grants_are_returned_with_stable_shape(self, app_manager, test_user):
        """Optimising the read path must not change the authorized-apps shape."""
        db = app_manager._db
        aid = 60_000_000
        db.execute(
            """INSERT INTO app_applications
               (id, owner_id, name, bot_id, redirect_uris, allowed_scopes,
                client_secret_hash, created_at, updated_at)
               VALUES (?, ?, ?, NULL, '[]', '[]', 'secret', 1, 1)""",
            (aid, test_user.id, "Authorized App"),
        )
        db.execute(
            """INSERT INTO app_oauth_tokens
               (id, application_id, user_id, access_token_hash, scopes, expires_at,
                created_at, revoked, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                70_000_000,
                aid,
                test_user.id,
                "hash",
                json.dumps(["identify"]),
                9_000_000_000_000,
                1,
                0,
                None,
            ),
        )
        with patch.object(
            bots_route.applications,
            "get_user_authorized_apps",
            side_effect=lambda uid: app_manager.get_user_authorized_apps(uid),
        ):
            result = asyncio.run(
                bots_route.list_authorized_apps(current_user=_Tok(test_user.id))
            )
        assert len(result) == 1
        app = result[0]
        assert app.application_id == str(aid)
        assert app.application_name == "Authorized App"
        assert app.scopes == ["identify"]
        assert app.last_used_at is None
