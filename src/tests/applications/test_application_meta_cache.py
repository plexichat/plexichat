"""Regression tests for the bounded short-TTL application metadata cache.

The cache must (1) return identical output while collapsing repeated
``get_application`` DB reads, (2) never bypass the per-caller ownership check,
(3) be invalidated by every mutation of ``app_applications``, and (4) respect
its entry/byte/TTL bounds. Assertions are on query counts and values rather
than wall-clock time so they stay deterministic.
"""

import pytest
from unittest.mock import patch

from src.core.applications.manager.lookup_cache import (
    CACHE_MISS,
    BoundedTTLCache,
)


@pytest.mark.applications
class TestApplicationMetadataCache:
    def test_repeated_lookup_is_identical_and_skips_sql(self, app_manager, user_pool):
        """A warm metadata row avoids every repeat DB round trip."""
        owner = user_pool.get_user()
        app = app_manager.create_application(owner.id, "Cached App")

        real_fetch_one = app_manager._db.fetch_one
        queries = []

        def tracking_fetch_one(query, params=None):
            queries.append(query)
            return real_fetch_one(query, params)

        with patch.object(app_manager._db, "fetch_one", side_effect=tracking_fetch_one):
            first = app_manager.get_application(app.id)
            queries.clear()
            second = app_manager.get_application(app.id)
            third = app_manager.get_application(app.id)

        assert first == second == third
        assert queries == []  # three reads, one DB query total
        assert app_manager._app_meta_cache.stats()["hits"] >= 2

    def test_cache_hit_still_enforces_owner_authz(self, app_manager, user_pool):
        """Caching the shared row must never skip the ownership check."""
        from src.core.applications.exceptions import ApplicationAccessDeniedError

        owner = user_pool.get_user()
        other = user_pool.get_user()
        app = app_manager.create_application(owner.id, "Private Meta App")

        # Warm the cache with an unrestricted read first.
        assert app_manager.get_application(app.id) is not None
        assert app_manager.get_application(app.id, owner.id).id == app.id
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.get_application(app.id, other.id)

    def test_update_invalidates_cached_metadata(self, app_manager, user_pool):
        """An update is visible through the very next read."""
        owner = user_pool.get_user()
        app = app_manager.create_application(owner.id, "Before")
        assert app_manager.get_application(app.id).name == "Before"  # warm

        updated = app_manager.update_application(owner.id, app.id, name="After")

        assert updated.name == "After"
        assert app_manager.get_application(app.id).name == "After"

    def test_delete_invalidates_cached_metadata(self, app_manager, user_pool):
        """A deleted application must not survive as a cached ghost."""
        owner = user_pool.get_user()
        app = app_manager.create_application(owner.id, "To Delete")
        assert app_manager.get_application(app.id) is not None  # warm

        assert app_manager.delete_application(owner.id, app.id) is True
        assert app_manager.get_application(app.id) is None

    def test_every_write_call_site_invalidates(
        self, app_manager, auth_manager, user_pool
    ):
        """Each mutation of app_applications drops the cached row."""
        owner = user_pool.get_user()
        app = app_manager.create_application(owner.id, "Writes")
        # The default fixture omits the auth module; bot creation needs one.
        app_manager._auth = auth_manager

        with patch.object(
            app_manager,
            "_invalidate_app_meta_cache",
            wraps=app_manager._invalidate_app_meta_cache,
        ) as spy:

            def invalidated_this_app():
                return any(call.args[0] == app.id for call in spy.call_args_list)

            app_manager.update_application(owner.id, app.id, name="Renamed")
            assert invalidated_this_app()
            spy.reset_mock()

            app_manager.regenerate_client_secret(owner.id, app.id)
            assert invalidated_this_app()
            spy.reset_mock()

            app_manager.create_bot_for_application(owner.id, app.id)
            assert invalidated_this_app()
            spy.reset_mock()

            app_manager.delete_application(owner.id, app.id)
            assert invalidated_this_app()

    def test_cached_metadata_is_not_aliased_to_callers(self, app_manager, user_pool):
        """Mutating a returned row cannot corrupt the cached snapshot."""
        owner = user_pool.get_user()
        app = app_manager.create_application(
            owner.id, "Alias", redirect_uris=["https://example.com/cb"]
        )

        first = app_manager.get_application(app.id)
        first.redirect_uris.append("https://evil.example/cb")
        first.allowed_scopes.clear()

        second = app_manager.get_application(app.id)
        assert second.redirect_uris == ["https://example.com/cb"]
        assert second.allowed_scopes  # cached ceiling list was not cleared


@pytest.mark.applications
class TestBoundedTTLCache:
    def test_entry_cap_evicts_lru(self):
        cache = BoundedTTLCache(max_entries=2, max_bytes=1000, ttl_seconds=60)
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.get("a") == 1  # "b" is now least-recently-used
        cache.put("c", 3)
        assert cache.get("b") is CACHE_MISS
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.stats()["evictions"] == 1

    def test_byte_cap_evicts_and_skips_oversized_value(self):
        cache = BoundedTTLCache(
            max_entries=10, max_bytes=10, ttl_seconds=60, sizer=lambda v: len(v)
        )
        cache.put("a", "123456")
        cache.put("b", "123456")  # 12 bytes > 10 -> evict oldest
        assert cache.get("a") is CACHE_MISS
        assert cache.get("b") == "123456"
        assert cache.put("huge", "x" * 11) is False
        assert cache.get("huge") is CACHE_MISS

    def test_ttl_expiry_and_explicit_invalidation(self):
        now = [0.0]
        cache = BoundedTTLCache(
            max_entries=10,
            max_bytes=1000,
            ttl_seconds=1.0,
            clock=lambda: now[0],
        )
        cache.put("a", 1)
        assert cache.get("a") == 1
        now[0] = 1.5
        assert cache.get("a") is CACHE_MISS  # expired

        cache.put("b", 2)
        cache.invalidate("b")
        assert cache.get("b") is CACHE_MISS
