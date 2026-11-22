"""Regression tests for a bounded, per-manager bot-directory cache.

The directory slice cache used to be a module-global dict. Every
``ApplicationManager`` in the process shared it, so a manager wired to one DB
could be served a slice built from another manager's DB (cross-tenant metadata
bleed). These tests pin down that the cache is per-manager, that repeated reads
collapse into a single round trip, and that a real mutation invalidates the
slice without weakening the public-profile filter.
"""

from unittest.mock import patch

import pytest

from src.core.applications.manager.bot_management import (
    BotManagementMixin,
    invalidate_bot_directory_cache,
)


@pytest.fixture(autouse=True)
def _reset_directory_cache():
    """Keep the compatibility broadcast shim from leaking between tests."""
    invalidate_bot_directory_cache()
    yield
    invalidate_bot_directory_cache()


class _FakeDB:
    """Minimal DB double returning a fixed directory page."""

    def __init__(self, row):
        self._row = row

    def fetch_one(self, query, params=None):
        return {"total": 1 if self._row else 0}

    def fetch_all(self, query, params=None):
        limit, offset = params[-2], params[-1]
        return [self._row] if self._row and offset == 0 and limit > 0 else []

    def execute(self, *args, **kwargs):
        return None


class _MiniManager(BotManagementMixin):
    """A manager instance with only the state the directory path needs."""

    def __init__(self, db):
        self._db = db


def _directory_row(app_id, name, private=0):
    return {
        "id": app_id,
        "name": name,
        "description": name,
        "icon_url": None,
        "bot_id": app_id + 1000,
        "short_description": name,
        "tags": "[]",
        "nsfw": 0,
        "private": private,
    }


@pytest.mark.applications
class TestPerManagerDirectoryCache:
    def test_repeated_directory_read_skips_sql(self, app_manager):
        """A warm per-manager slice avoids the COUNT and the page query."""
        real_fetch_all = app_manager._db.fetch_all
        real_fetch_one = app_manager._db.fetch_one
        queries = []

        def tracking_fetch_all(query, params=None):
            queries.append(query)
            return real_fetch_all(query, params)

        def tracking_fetch_one(query, params=None):
            queries.append(query)
            return real_fetch_one(query, params)

        with (
            patch.object(app_manager._db, "fetch_all", side_effect=tracking_fetch_all),
            patch.object(app_manager._db, "fetch_one", side_effect=tracking_fetch_one),
        ):
            first = app_manager.get_bot_directory()
            assert len(queries) == 2  # COUNT + page on a cold read
            queries.clear()
            second = app_manager.get_bot_directory()

        assert queries == []  # warm read: no SQL at all
        assert second == first
        assert app_manager._get_directory_cache().stats()["hits"] >= 1

    def test_mutation_invalidates_cached_slice(
        self, app_manager, auth_manager, test_user
    ):
        """A directory-visible write refreshes the slice without manual help."""
        db = app_manager._db
        app = app_manager.create_application(owner_id=test_user.id, name="Cached Bot")
        bot = auth_manager.create_bot(
            owner_id=test_user.id, username="cached_perf_bot", display_name="Cached Bot"
        )
        db.execute(
            "UPDATE app_applications SET bot_id = ? WHERE id = ?", (bot.id, app.id)
        )
        db.execute(
            """INSERT INTO app_bot_profiles
               (application_id, short_description, tags, nsfw, private, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (app.id, "before", "[]", 0, 0, 1),
        )

        assert app_manager.get_bot_directory()["bot_list"][0]["description"] == "before"

        # A real mutation (profile save) calls the per-manager invalidation.
        app_manager.update_bot_profile(app.id, test_user.id, short_description="after")

        refreshed = app_manager.get_bot_directory()
        assert refreshed["bot_list"][0]["description"] == "after"

    def test_no_cross_manager_leak(self):
        """Two managers never share a slice, even with identical cache keys."""
        a = _MiniManager(_FakeDB(_directory_row(1, "AliceBot")))
        b = _MiniManager(_FakeDB(_directory_row(2, "BobBot")))

        assert a.get_bot_directory()["bot_list"][0]["name"] == "AliceBot"
        # A module-global cache served Alice's slice to manager B here.
        assert b.get_bot_directory()["bot_list"][0]["name"] == "BobBot"
        assert a._get_directory_cache() is not b._get_directory_cache()

    def test_public_filter_keeps_private_bots_hidden(
        self, app_manager, auth_manager, test_user
    ):
        """Private bots stay out of public results across cache keys."""
        db = app_manager._db
        app = app_manager.create_application(owner_id=test_user.id, name="Hidden Bot")
        bot = auth_manager.create_bot(
            owner_id=test_user.id, username="hidden_perf_bot", display_name="Hidden Bot"
        )
        db.execute(
            "UPDATE app_applications SET bot_id = ? WHERE id = ?", (bot.id, app.id)
        )
        db.execute(
            """INSERT INTO app_bot_profiles
               (application_id, short_description, tags, nsfw, private, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (app.id, "secret", "[]", 0, 1, 1),
        )

        # An internal caller asking for all bots sees the private entry.
        all_names = [
            b["name"]
            for b in app_manager.get_bot_directory(include_public=False)["bot_list"]
        ]
        assert "Hidden Bot" in all_names
        # The public route's key is distinct and must never reuse that slice.
        public_names = [
            b["name"]
            for b in app_manager.get_bot_directory(include_public=True)["bot_list"]
        ]
        assert "Hidden Bot" not in public_names
