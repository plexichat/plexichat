"""
Tests for sticker suggestions.
"""

from src.core.stickers import StickerFormat


class TestStickerSuggestions:
    """Tests for sticker suggestion functionality."""

    def test_suggestions_by_name(self, server_with_pack):
        """Test suggestions match sticker names."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="happy",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/happy.png",
            size=100000,
        )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="I am so happy today!", server_id=server.id
        )

        names = [s.sticker.name for s in suggestions]
        assert "happy" in names

    def test_suggestions_by_tags(self, server_with_pack):
        """Test suggestions match sticker tags."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="cute_cat",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/cute_cat.png",
            size=100000,
            tags=["cute", "adorable", "cat"],
        )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="That is so cute!", server_id=server.id
        )

        names = [s.sticker.name for s in suggestions]
        assert "cute_cat" in names

    def test_suggestions_by_emoji(self, server_with_pack):
        """Test suggestions match related emoji."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="thumbs_up_sticker",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/thumbs.png",
            size=100000,
            related_emoji="thumbsup",
        )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="Great job! thumbsup", server_id=server.id
        )

        names = [s.sticker.name for s in suggestions]
        assert "thumbs_up_sticker" in names

    def test_suggestions_empty_content(self, server_with_pack):
        """Test empty content returns no suggestions."""
        owner, server, pack, stickers, servers = server_with_pack

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="", server_id=server.id
        )

        assert len(suggestions) == 0

    def test_suggestions_no_matches(self, server_with_pack):
        """Test content with no matches returns empty list."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="specific_sticker",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/specific.png",
            size=100000,
            tags=["specific", "unique"],
        )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id,
            content="completely unrelated content xyz",
            server_id=server.id,
        )

        names = [s.sticker.name for s in suggestions]
        assert "specific_sticker" not in names

    def test_suggestions_limit(self, server_with_pack):
        """Test suggestions respect limit parameter."""
        owner, server, pack, stickers, servers = server_with_pack

        for i in range(15):
            stickers.add_sticker(
                user_id=owner.id,
                pack_id=pack.id,
                name=f"test_sticker_{i}",
                format=StickerFormat.PNG,
                url=f"https://cdn.example.com/stickers/test_{i}.png",
                size=100000,
                tags=["test"],
            )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="test", server_id=server.id, limit=5
        )

        assert len(suggestions) <= 5

    def test_suggestions_sorted_by_relevance(self, server_with_pack):
        """Test suggestions are sorted by relevance score."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="exact_match",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/exact.png",
            size=100000,
        )

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="partial",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/partial.png",
            size=100000,
            tags=["exact"],
        )

        suggestions = stickers.get_sticker_suggestions(
            user_id=owner.id, content="exact_match is what I want", server_id=server.id
        )

        if len(suggestions) >= 2:
            assert suggestions[0].relevance_score >= suggestions[1].relevance_score
