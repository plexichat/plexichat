"""
Tests for Unicode emoji handling.
"""

import pytest
from src.core.reactions import InvalidEmojiError


class TestUnicodeEmoji:
    """Tests for Unicode emoji reactions."""

    def test_simple_emoji(self, fresh_users_with_dm_and_relationships):
        """Test simple text emoji."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "thumbsup")

        assert reaction.emoji == "thumbsup"
        assert reaction.is_custom is False

    def test_emoji_with_spaces_trimmed(self, fresh_users_with_dm_and_relationships):
        """Test emoji with spaces is trimmed."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "  heart  ")

        assert reaction.emoji == "heart"

    def test_empty_emoji_fails(self, fresh_users_with_dm_and_relationships):
        """Test empty emoji fails."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, "")

    def test_whitespace_only_emoji_fails(self, fresh_users_with_dm_and_relationships):
        """Test whitespace-only emoji fails."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, "   ")

    def test_very_long_emoji_fails(self, fresh_users_with_dm_and_relationships):
        """Test very long emoji string fails."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        long_emoji = "a" * 100

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, long_emoji)

    def test_max_length_emoji(self, fresh_users_with_dm_and_relationships):
        """Test emoji at max length (32 chars) succeeds."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        max_emoji = "a" * 32
        reaction = reaction_manager.add_reaction(user1.id, msg.id, max_emoji)

        assert reaction.emoji == max_emoji

    @pytest.mark.slow
    def test_various_emoji_names(self, fresh_users_with_dm_and_relationships):
        """Test various common emoji names."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        emoji_names = ["smile", "heart", "fire", "100", "clap", "eyes", "rocket"]

        for emoji in emoji_names:
            reaction = reaction_manager.add_reaction(user1.id, msg.id, emoji)
            assert reaction.emoji == emoji

    @pytest.mark.slow
    def test_emoji_case_sensitive(self, fresh_users_with_dm_and_relationships):
        """Test emoji names are case sensitive."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        r1 = reaction_manager.add_reaction(user1.id, msg.id, "Heart")
        r2 = reaction_manager.add_reaction(user1.id, msg.id, "heart")

        assert r1.emoji == "Heart"
        assert r2.emoji == "heart"
        assert r1.id != r2.id

    @pytest.mark.slow
    def test_emoji_with_numbers(self, fresh_users_with_dm_and_relationships):
        """Test emoji names with numbers."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "100")

        assert reaction.emoji == "100"

    @pytest.mark.slow
    def test_emoji_with_underscores(self, fresh_users_with_dm_and_relationships):
        """Test emoji names with underscores."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "thumbs_up")

        assert reaction.emoji == "thumbs_up"

    @pytest.mark.slow
    def test_emoji_with_colons(self, fresh_users_with_dm_and_relationships):
        """Test emoji names with colons are rejected (reserved for custom emoji format)."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, ":smile:")
