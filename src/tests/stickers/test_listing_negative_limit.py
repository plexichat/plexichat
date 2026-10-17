"""Regression tests: sticker listing ``limit`` must be clamped from below.

``get_popular_stickers`` / ``get_recent_stickers`` / ``search_stickers``
passed ``limit`` straight into the SQL ``LIMIT ?``, and SQLite treats
``LIMIT -1`` as *unlimited* — so a negative ``limit`` dumped the whole
sticker table instead of an empty page. The manager clamps from below
(``max(0, limit)``) so a negative limit yields ``LIMIT 0`` — an empty page —
while every non-negative limit behaves exactly as before.
"""

from src.core.stickers.models import PackType, StickerFormat


def _make_stickers(sticker_manager, count=3):
    pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
    stickers = []
    for i in range(count):
        sticker = sticker_manager.add_sticker(
            1, pack.id, f"sticker{i}", StickerFormat.PNG, f"url{i}", 1000
        )
        stickers.append(sticker)
        sticker_manager.record_usage(1, sticker.id)
    return stickers


class TestNegativeLimitClamp:
    def test_get_popular_negative_limit_returns_empty(self, sticker_manager):
        """A negative limit must not bypass the page cap (LIMIT -1 = all)."""
        _make_stickers(sticker_manager)
        assert sticker_manager.get_popular_stickers(limit=-1) == []

    def test_get_popular_zero_limit_returns_empty(self, sticker_manager):
        _make_stickers(sticker_manager)
        assert sticker_manager.get_popular_stickers(limit=0) == []

    def test_get_recent_negative_limit_returns_empty(self, sticker_manager):
        """A negative limit must not dump the caller's usage history."""
        _make_stickers(sticker_manager)
        assert sticker_manager.get_recent_stickers(1, limit=-1) == []

    def test_search_negative_limit_returns_empty(self, sticker_manager):
        """A negative limit must not dump every sticker matching the query."""
        _make_stickers(sticker_manager)
        assert sticker_manager.search_stickers("sticker", limit=-1) == []

    def test_suggestions_negative_limit_returns_empty(self, sticker_manager):
        """A negative limit must not return (almost) every match.

        Pre-fix the negative limit flowed into ``suggestions[:limit]``, so
        ``limit=-1`` returned every match but the last instead of nothing.
        """
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker_manager.add_sticker(
            1, pack.id, "happy", StickerFormat.PNG, "url1", 1000
        )
        sticker_manager.add_sticker(1, pack.id, "joy", StickerFormat.PNG, "url2", 1000)
        suggestions = sticker_manager.get_sticker_suggestions(1, "happy joy", limit=-1)
        assert suggestions == []
        # Sanity: both stickers do match, so the empty page is the clamp.
        assert len(sticker_manager.get_sticker_suggestions(1, "happy joy")) == 2

    def test_non_negative_limits_behave_as_before(self, sticker_manager):
        stickers = _make_stickers(sticker_manager)
        assert len(sticker_manager.get_popular_stickers(limit=2)) == 2
        assert len(sticker_manager.get_popular_stickers(limit=50)) == len(stickers)
        assert len(sticker_manager.get_recent_stickers(1, limit=50)) == len(stickers)
        assert len(sticker_manager.search_stickers("sticker", limit=50)) == len(
            stickers
        )
