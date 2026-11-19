"""Regression: sticker discovery ``limit`` must be bounded at the core boundary.

The HTTP layer validates ``limit`` (e.g. ``/stickers/suggestions`` is
``ge=1, le=20``), but the manager methods are public internal APIs reached by
callers that bypass those schemas. They used to clamp only from below
(``max(0, limit)``): ``LIMIT -1`` meant "unlimited" in SQLite and an oversized
value forced the whole result set to be materialised. Every limit now flows
through ``_bounded_listing_limit`` which bounds both directions and rejects
type-confused values.
"""

from src.core.stickers._discovery import MAX_DISCOVERY_LIMIT, _bounded_listing_limit


class TestBoundedListingLimitHelper:
    def test_in_range_limits_preserved(self):
        assert _bounded_listing_limit(0) == 0
        assert _bounded_listing_limit(1) == 1
        assert _bounded_listing_limit(50) == 50
        assert _bounded_listing_limit(MAX_DISCOVERY_LIMIT) == MAX_DISCOVERY_LIMIT

    def test_negative_limit_fails_closed_to_empty_page(self):
        assert _bounded_listing_limit(-1) == 0
        assert _bounded_listing_limit(-(10**9)) == 0

    def test_oversized_limit_is_capped(self):
        assert _bounded_listing_limit(10**9) == MAX_DISCOVERY_LIMIT
        assert _bounded_listing_limit(MAX_DISCOVERY_LIMIT + 1) == MAX_DISCOVERY_LIMIT

    def test_type_confusion_fails_closed(self):
        # bool is an int subclass but never a valid page size.
        assert _bounded_listing_limit(True) == 0
        assert _bounded_listing_limit(False) == 0
        # ASCII digit strings are accepted; homoglyphs/fractions are not.
        assert _bounded_listing_limit("50") == 50
        assert _bounded_listing_limit("٥٠") == 0
        assert _bounded_listing_limit(1.5) == 0
        assert _bounded_listing_limit(float("inf")) == 0
        # An absent limit keeps the documented default.
        assert _bounded_listing_limit(None) == 10


class TestStickerListingUpperBoundAtDb:
    """The manager must pass the capped value into SQL ``LIMIT ?``."""

    def _spy(self, sticker_manager, monkeypatch):
        captured = {}
        original = sticker_manager._db.fetch_all

        def spy(statement, params=()):
            if "LIMIT ?" in statement:
                captured["limit"] = params[-1]
            return original(statement, params)

        monkeypatch.setattr(sticker_manager._db, "fetch_all", spy)
        return captured

    def test_popular_limit_is_capped(self, sticker_manager, monkeypatch):
        captured = self._spy(sticker_manager, monkeypatch)
        sticker_manager.get_popular_stickers(limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT

    def test_recent_limit_is_capped(self, sticker_manager, monkeypatch):
        captured = self._spy(sticker_manager, monkeypatch)
        sticker_manager.get_recent_stickers(1, limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT

    def test_search_limit_is_capped(self, sticker_manager, monkeypatch):
        captured = self._spy(sticker_manager, monkeypatch)
        sticker_manager.search_stickers("sticker", limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT

    def test_suggestions_limit_is_capped(self, sticker_manager, monkeypatch):
        # Explicit 0 used to be swallowed by ``limit or config_default``;
        # it must now produce an empty page instead of the default page.
        assert sticker_manager.get_sticker_suggestions(1, "happy joy", limit=0) == []
        assert sticker_manager.get_sticker_suggestions(1, "happy joy", limit=-1) == []
        assert sticker_manager.get_sticker_suggestions(
            1, "happy joy", limit=10**9
        ) == sticker_manager.get_sticker_suggestions(
            1, "happy joy", limit=MAX_DISCOVERY_LIMIT
        )
