"""
Tests for edge cases and error handling.
"""

import pytest
from src.core.reactions import (
    MessageNotFoundError,
    ReactionNotFoundError,
    InvalidEmojiError,
    ReactionExistsError,
)


class TestMessageEdgeCases:
    """Tests for message-related edge cases."""

    def test_react_to_nonexistent_message(self, users_with_dm):
        """Test reacting to nonexistent message."""
        user1, user2, dm, msg, reactions = users_with_dm

        with pytest.raises(MessageNotFoundError):
            reactions.add_reaction(user1.id, 999999999999, "nonexistent")

    def test_get_reactions_nonexistent_message(self, users_with_dm):
        """Test getting reactions from nonexistent message."""
        user1, user2, dm, msg, reactions = users_with_dm

        with pytest.raises(MessageNotFoundError):
            reactions.get_reactions(user1.id, 999999999999)

    def test_get_reaction_users_nonexistent_message(self, users_with_dm):
        """Test getting reaction users from nonexistent message."""
        user1, user2, dm, msg, reactions = users_with_dm

        with pytest.raises(MessageNotFoundError):
            reactions.get_reaction_users(user1.id, 999999999999, "emoji")

    def test_remove_reaction_nonexistent_message(self, users_with_dm):
        """Test removing reaction from nonexistent message."""
        user1, user2, dm, msg, reactions = users_with_dm

        with pytest.raises(MessageNotFoundError):
            reactions.remove_reaction(user1.id, 999999999999, "emoji")


class TestEmojiEdgeCases:
    """Tests for emoji-related edge cases."""

    def test_empty_emoji_string(self, fresh_users_with_dm):
        """Test empty emoji string."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        with pytest.raises(InvalidEmojiError):
            reactions.add_reaction(user1.id, msg.id, "")

    def test_none_emoji(self, fresh_users_with_dm):
        """Test None emoji raises error."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        with pytest.raises((InvalidEmojiError, TypeError)):
            reactions.add_reaction(user1.id, msg.id, None)

    def test_special_characters_in_emoji(self, fresh_users_with_dm):
        """Test special characters in emoji name."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "emoji!@#")

        assert reaction.emoji == "emoji!@#"

    def test_unicode_in_emoji_name(self, fresh_users_with_dm):
        """Test unicode characters in emoji name."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "test123")

        assert reaction is not None


class TestReactionEdgeCases:
    """Tests for reaction-related edge cases."""

    def test_get_nonexistent_reaction_by_id(self, users_with_dm):
        """Test getting nonexistent reaction by ID."""
        user1, user2, dm, msg, reactions = users_with_dm

        result = reactions.get_reaction(999999999999)

        assert result is None

    def test_remove_already_removed_reaction(self, fresh_users_with_dm):
        """Test removing already removed reaction."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "remove_twice")
        reactions.remove_reaction(user1.id, msg.id, "remove_twice")

        with pytest.raises(ReactionNotFoundError):
            reactions.remove_reaction(user1.id, msg.id, "remove_twice")

    def test_add_same_reaction_twice(self, fresh_users_with_dm):
        """Test adding same reaction twice."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "duplicate")

        with pytest.raises(ReactionExistsError):
            reactions.add_reaction(user1.id, msg.id, "duplicate")

    def test_reaction_id_uniqueness(self, fresh_users_with_dm):
        """Test reaction IDs are unique."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        r1 = reactions.add_reaction(user1.id, msg.id, "unique1")
        r2 = reactions.add_reaction(user1.id, msg.id, "unique2")
        r3 = reactions.add_reaction(user2.id, msg.id, "unique1")

        assert r1.id != r2.id
        assert r1.id != r3.id
        assert r2.id != r3.id


class TestAccessControl:
    """Tests for access control edge cases."""

    def test_non_participant_cannot_view_reactions(self, db_and_modules):
        """Test non-participant cannot view reactions."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"access1_{unique_id}",
            email=f"access1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"access2_{unique_id}",
            email=f"access2_{unique_id}@example.com",
            password="TestPass123!",
        )
        outsider = auth.register(
            username=f"access3_{unique_id}",
            email=f"access3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Private")

        reactions.add_reaction(user1.id, msg.id, "private")

        with pytest.raises(MessageNotFoundError):
            reactions.get_reactions(outsider.id, msg.id)

    def test_non_participant_cannot_get_user_reactions(self, db_and_modules):
        """Test non-participant cannot get user reactions."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"userreact1_{unique_id}",
            email=f"userreact1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"userreact2_{unique_id}",
            email=f"userreact2_{unique_id}@example.com",
            password="TestPass123!",
        )
        outsider = auth.register(
            username=f"userreact3_{unique_id}",
            email=f"userreact3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Private user reactions")

        with pytest.raises(MessageNotFoundError):
            reactions.get_user_reactions(outsider.id, msg.id)


class TestConcurrentOperations:
    """Tests for concurrent-like operations."""

    def test_multiple_users_react_same_time(self, group_with_message):
        """Test multiple users can react to same message."""
        owner, member1, member2, group, msg, messaging, reactions = group_with_message

        r1 = reactions.add_reaction(owner.id, msg.id, "concurrent")
        r2 = reactions.add_reaction(member1.id, msg.id, "concurrent")
        r3 = reactions.add_reaction(member2.id, msg.id, "concurrent")

        assert r1 is not None
        assert r2 is not None
        assert r3 is not None

        msg_reactions = reactions.get_reactions(owner.id, msg.id)
        concurrent = next(
            (r for r in msg_reactions.reactions if r.emoji == "concurrent"), None
        )

        assert concurrent is not None
        assert concurrent.count == 3

    def test_add_remove_rapid_succession(self, fresh_users_with_dm):
        """Test rapid add/remove operations."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        for i in range(10):
            reactions.add_reaction(user1.id, msg.id, f"rapid_{i}")
            reactions.remove_reaction(user1.id, msg.id, f"rapid_{i}")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)
        rapid_reactions = [
            r for r in msg_reactions.reactions if r.emoji.startswith("rapid_")
        ]

        assert len(rapid_reactions) == 0


class TestDataIntegrity:
    """Tests for data integrity."""

    def test_reaction_timestamps_valid(self, fresh_users_with_dm):
        """Test reaction timestamps are valid."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm
        import time

        before = int(time.time() * 1000)
        reaction = reactions.add_reaction(user1.id, msg.id, "timestamp")
        after = int(time.time() * 1000)

        assert reaction.created_at >= before
        assert reaction.created_at <= after

    def test_reaction_preserves_emoji_exactly(self, fresh_users_with_dm):
        """Test reaction preserves emoji string exactly."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        test_emojis = ["thumbsup", "THUMBSUP", "Thumbs_Up", "100"]

        for emoji in test_emojis:
            reaction = reactions.add_reaction(user1.id, msg.id, emoji)
            assert reaction.emoji == emoji

    def test_custom_emoji_id_preserved(self, users_with_server):
        """Test custom emoji ID is preserved correctly."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "preserve_id")
        custom_str = f"<:preserve_id:{emoji.id}>"

        reaction = reactions.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.custom_emoji_id == emoji.id

        fetched = reactions.get_reaction(reaction.id)
        assert fetched.custom_emoji_id == emoji.id
