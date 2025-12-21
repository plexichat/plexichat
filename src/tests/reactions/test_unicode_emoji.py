"""
Tests for Unicode emoji handling.
"""

import pytest
from src.core.reactions import InvalidEmojiError


class TestUnicodeEmoji:
    """Tests for Unicode emoji reactions."""

    def test_simple_emoji(self, fresh_users_with_dm):
        """Test simple text emoji."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "thumbsup")

        assert reaction.emoji == "thumbsup"
        assert reaction.is_custom is False

    def test_emoji_with_spaces_trimmed(self, fresh_users_with_dm):
        """Test emoji with spaces is trimmed."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "  heart  ")

        assert reaction.emoji == "heart"

    def test_empty_emoji_fails(self, fresh_users_with_dm):
        """Test empty emoji fails."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        with pytest.raises(InvalidEmojiError):
            reactions.add_reaction(user1.id, msg.id, "")

    def test_whitespace_only_emoji_fails(self, fresh_users_with_dm):
        """Test whitespace-only emoji fails."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        with pytest.raises(InvalidEmojiError):
            reactions.add_reaction(user1.id, msg.id, "   ")

    def test_very_long_emoji_fails(self, fresh_users_with_dm):
        """Test very long emoji string fails."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        long_emoji = "a" * 100

        with pytest.raises(InvalidEmojiError):
            reactions.add_reaction(user1.id, msg.id, long_emoji)

    def test_max_length_emoji(self, fresh_users_with_dm):
        """Test emoji at max length (32 chars) succeeds."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        max_emoji = "a" * 32
        reaction = reactions.add_reaction(user1.id, msg.id, max_emoji)

        assert reaction.emoji == max_emoji

    def test_various_emoji_names(self, fresh_users_with_dm):
        """Test various common emoji names."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        emoji_names = ["smile", "heart", "fire", "100", "clap", "eyes", "rocket"]

        for emoji in emoji_names:
            reaction = reactions.add_reaction(user1.id, msg.id, emoji)
            assert reaction.emoji == emoji

    def test_emoji_case_sensitive(self, fresh_users_with_dm):
        """Test emoji names are case sensitive."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        r1 = reactions.add_reaction(user1.id, msg.id, "Heart")
        r2 = reactions.add_reaction(user1.id, msg.id, "heart")

        assert r1.emoji == "Heart"
        assert r2.emoji == "heart"
        assert r1.id != r2.id

    def test_emoji_with_numbers(self, fresh_users_with_dm):
        """Test emoji names with numbers."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "100")

        assert reaction.emoji == "100"

    def test_emoji_with_underscores(self, fresh_users_with_dm):
        """Test emoji names with underscores."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "thumbs_up")

        assert reaction.emoji == "thumbs_up"

    def test_emoji_with_colons(self, fresh_users_with_dm):
        """Test emoji names with colons (not custom format)."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, ":smile:")

        assert reaction.emoji == ":smile:"
        assert reaction.is_custom is False
