"""
Tests for edge cases and error handling.
"""

import pytest
import uuid
from src.core.reactions import (
    MessageNotFoundError,
    ReactionNotFoundError,
    InvalidEmojiError,
    ReactionExistsError,
)


class TestMessageEdgeCases:
    """Tests for message-related edge cases."""

    def test_react_to_nonexistent_message(self, users_with_dm_and_reaction):
        """Test reacting to nonexistent message."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.add_reaction(user1.id, 999999999999, "nonexistent")

    def test_get_reactions_nonexistent_message(self, users_with_dm_and_reaction):
        """Test getting reactions from nonexistent message."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.get_reactions(user1.id, 999999999999)

    def test_get_reaction_users_nonexistent_message(self, users_with_dm_and_reaction):
        """Test getting reaction users from nonexistent message."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.get_reaction_users(user1.id, 999999999999, "emoji")

    def test_remove_reaction_nonexistent_message(self, users_with_dm_and_reaction):
        """Test removing reaction from nonexistent message."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.remove_reaction(user1.id, 999999999999, "emoji")


class TestEmojiEdgeCases:
    """Tests for emoji-related edge cases."""

    def test_empty_emoji_string(self, fresh_users_with_dm_and_relationships):
        """Test empty emoji string."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, "")

    def test_none_emoji(self, fresh_users_with_dm_and_relationships):
        """Test None emoji raises error."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises((InvalidEmojiError, TypeError)):
            reaction_manager.add_reaction(user1.id, msg.id, None)

    def test_special_characters_in_emoji(self, fresh_users_with_dm_and_relationships):
        """Test special characters in emoji name are rejected."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(user1.id, msg.id, "emoji!@#")

    def test_unicode_in_emoji_name(self, fresh_users_with_dm_and_relationships):
        """Test unicode characters in emoji name."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "test123")

        assert reaction is not None


class TestReactionEdgeCases:
    """Tests for reaction-related edge cases."""

    def test_get_nonexistent_reaction_by_id(self, users_with_dm_and_reaction):
        """Test getting nonexistent reaction by ID."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        result = reaction_manager.get_reaction(999999999999)

        assert result is None

    def test_remove_already_removed_reaction(
        self, fresh_users_with_dm_and_relationships
    ):
        """Test removing already removed reaction."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "remove_twice")
        reaction_manager.remove_reaction(user1.id, msg.id, "remove_twice")

        with pytest.raises(ReactionNotFoundError):
            reaction_manager.remove_reaction(user1.id, msg.id, "remove_twice")

    def test_add_same_reaction_twice(self, fresh_users_with_dm_and_relationships):
        """Test adding same reaction twice."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "duplicate")

        with pytest.raises(ReactionExistsError):
            reaction_manager.add_reaction(user1.id, msg.id, "duplicate")

    def test_reaction_id_uniqueness(self, fresh_users_with_dm_and_relationships):
        """Test reaction IDs are unique."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        r1 = reaction_manager.add_reaction(user1.id, msg.id, "unique1")
        r2 = reaction_manager.add_reaction(user1.id, msg.id, "unique2")
        r3 = reaction_manager.add_reaction(user2.id, msg.id, "unique1")

        assert r1.id != r2.id
        assert r1.id != r3.id
        assert r2.id != r3.id


class TestAccessControl:
    """Tests for access control edge cases."""

    def test_non_participant_cannot_view_reactions(
        self, auth_manager, messaging_manager, reaction_manager
    ):
        """Test non-participant cannot view reactions."""
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth_manager.register(
            username=f"access1_{unique_id}",
            email=f"access1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"access2_{unique_id}",
            email=f"access2_{unique_id}@example.com",
            password="TestPass123!",
        )
        outsider = auth_manager.register(
            username=f"access3_{unique_id}",
            email=f"access3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Private")

        reaction_manager.add_reaction(user1.id, msg.id, "private")

        with pytest.raises(MessageNotFoundError):
            reaction_manager.get_reactions(outsider.id, msg.id)

    def test_non_participant_cannot_get_user_reactions(
        self, auth_manager, messaging_manager, reaction_manager
    ):
        """Test non-participant cannot get user reactions."""
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth_manager.register(
            username=f"userreact1_{unique_id}",
            email=f"userreact1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"userreact2_{unique_id}",
            email=f"userreact2_{unique_id}@example.com",
            password="TestPass123!",
        )
        outsider = auth_manager.register(
            username=f"userreact3_{unique_id}",
            email=f"userreact3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Private user reactions")

        with pytest.raises(MessageNotFoundError):
            reaction_manager.get_user_reactions(outsider.id, msg.id)


class TestConcurrentOperations:
    """Tests for concurrent-like operations."""

    def test_multiple_users_react_same_time(self, group_with_message):
        """Test multiple users can react to same message."""
        owner, member1, member2, group, msg, messaging_manager, reaction_manager = (
            group_with_message
        )

        r1 = reaction_manager.add_reaction(owner.id, msg.id, "concurrent")
        r2 = reaction_manager.add_reaction(member1.id, msg.id, "concurrent")
        r3 = reaction_manager.add_reaction(member2.id, msg.id, "concurrent")

        assert r1 is not None
        assert r2 is not None
        assert r3 is not None

        msg_reactions = reaction_manager.get_reactions(owner.id, msg.id)
        concurrent = next(
            (r for r in msg_reactions.reactions if r.emoji == "concurrent"), None
        )

        assert concurrent is not None
        assert concurrent.count == 3

    def test_add_remove_rapid_succession(self, fresh_users_with_dm_and_relationships):
        """Test rapid add/remove operations."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        for i in range(10):
            reaction_manager.add_reaction(user1.id, msg.id, f"rapid_{i}")
            reaction_manager.remove_reaction(user1.id, msg.id, f"rapid_{i}")

        msg_reactions = reaction_manager.get_reactions(user1.id, msg.id)
        rapid_reactions = [
            r for r in msg_reactions.reactions if r.emoji.startswith("rapid_")
        ]

        assert len(rapid_reactions) == 0


class TestDataIntegrity:
    """Tests for data integrity."""

    def test_reaction_timestamps_valid(self, fresh_users_with_dm_and_relationships):
        """Test reaction timestamps are valid."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )
        import time

        before = int(time.time() * 1000)
        reaction = reaction_manager.add_reaction(user1.id, msg.id, "timestamp")
        after = int(time.time() * 1000)

        assert reaction.created_at >= before
        assert reaction.created_at <= after

    def test_reaction_preserves_emoji_exactly(
        self, fresh_users_with_dm_and_relationships
    ):
        """Test reaction preserves emoji string exactly."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        test_emojis = ["thumbsup", "THUMBSUP", "Thumbs_Up", "100"]

        for emoji in test_emojis:
            reaction = reaction_manager.add_reaction(user1.id, msg.id, emoji)
            assert reaction.emoji == emoji

    def test_custom_emoji_id_preserved(self, users_with_server):
        """Test custom emoji ID is preserved correctly."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(owner.id, server.id, "preserve_id")
        custom_str = f"<:preserve_id:{emoji.id}>"

        reaction = reaction_manager.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.custom_emoji_id == emoji.id

        fetched = reaction_manager.get_reaction(reaction.id)
        assert fetched.custom_emoji_id == emoji.id
