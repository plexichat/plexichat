"""Focused tests for emoji route helpers."""

import src.api.routes.emojis as emoji_routes


class TestEmojiCacheInvalidation:
    """Tests for emoji cache invalidation helpers."""

    def test_invalidate_emoji_cache_clears_server_list_only(self, monkeypatch):
        """Test list cache invalidation without an emoji ID."""
        invalidated = []
        monkeypatch.setattr(emoji_routes, "invalidate_pattern", invalidated.append)

        emoji_routes._invalidate_emoji_cache(123)

        assert invalidated == [
            f"{emoji_routes.get_server_emojis.cache_key_prefix}:str:123:*"
        ]

    def test_invalidate_emoji_cache_clears_server_list_and_detail(self, monkeypatch):
        """Test list and detail cache invalidation with an emoji ID."""
        invalidated = []
        monkeypatch.setattr(emoji_routes, "invalidate_pattern", invalidated.append)

        emoji_routes._invalidate_emoji_cache(123, 456)

        assert invalidated == [
            f"{emoji_routes.get_server_emojis.cache_key_prefix}:str:123:*",
            f"{emoji_routes.get_emoji.cache_key_prefix}:str:123:str:456:*",
        ]