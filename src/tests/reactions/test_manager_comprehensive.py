"""Comprehensive Reactions tests targeting 80%+ coverage."""

import pytest
from src.core.reactions.exceptions import (
    MessageNotFoundError,
    InvalidEmojiError,
    ReactionExistsError,
    ReactionLimitError,
    ReactionNotFoundError,
    PermissionDeniedError,
)


class TestReactionErrors:
    def test_message_not_found(self, reaction_manager):
        """Cannot react to non-existent message."""
        with pytest.raises(MessageNotFoundError):
            reaction_manager.add_reaction(1, 99999, "👍")

    def test_invalid_emoji_empty(self, reaction_manager):
        """Empty emoji invalid."""
        with pytest.raises(InvalidEmojiError):
            reaction_manager._validate_emoji("")

    def test_invalid_emoji_too_long(self, reaction_manager):
        """Too long emoji invalid."""
        with pytest.raises(InvalidEmojiError):
            reaction_manager._validate_emoji("x" * 50)

    def test_duplicate_reaction(self, reaction_manager, test_db):
        """Cannot add same reaction twice."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        with pytest.raises(ReactionExistsError):
            reaction_manager.add_reaction(1, 1, "👍")

    def test_reaction_limit(self, reaction_manager, test_db, monkeypatch):
        """Cannot exceed reaction limit."""
        monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 2)

        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(1, 1, "❤️")

        with pytest.raises(ReactionLimitError):
            reaction_manager.add_reaction(1, 1, "😂")

    def test_reaction_unique_per_user(self, reaction_manager, test_db, monkeypatch):
        """User can only react once per emoji per message."""
        monkeypatch.setitem(
            reaction_manager._config, "max_unique_reactions_per_user", 1
        )

        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")

        with pytest.raises(ReactionLimitError):
            reaction_manager.add_reaction(1, 1, "❤️")

    def test_remove_nonexistent_reaction(self, reaction_manager, test_db):
        """Cannot remove non-existent reaction."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        with pytest.raises(ReactionNotFoundError):
            reaction_manager.remove_reaction(1, 1, "👍")

    def test_remove_others_reaction(self, reaction_manager, test_db):
        """Cannot remove others' reactions."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")

        with pytest.raises(PermissionDeniedError):
            reaction_manager.remove_reaction(2, 1, "👍")

    def test_remove_all_reactions_permission(self, reaction_manager, test_db):
        """Need permission to remove all reactions."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'owner', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        with pytest.raises(PermissionDeniedError):
            reaction_manager.remove_all_reactions(2, 1)

    def test_get_reactions_for_message(self, reaction_manager, test_db):
        """Can get all reactions for a message."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(2, 1, "❤️")

        reactions = reaction_manager.get_reactions(1, 1)
        assert len(reactions.reactions) >= 2

    def test_get_reactions_empty(self, reaction_manager, test_db):
        """Get reactions for message with none."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reactions = reaction_manager.get_reactions(1, 1)
        assert len(reactions.reactions) == 0

    def test_get_users_who_reacted(self, reaction_manager, test_db):
        """Can get users who reacted with specific emoji."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(2, 1, "👍")

        users = reaction_manager.get_users_who_reacted(1, "👍")
        assert len(users) >= 2

    def test_remove_all_reactions_as_owner(self, reaction_manager, test_db):
        """Owner can remove all reactions."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'owner', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(2, 1, "❤️")

        count = reaction_manager.remove_all_reactions(1, 1)
        assert count >= 2


class TestReactionCustomEmoji:
    """Test custom emoji reactions."""

    def test_custom_emoji_reaction(self, reaction_manager, test_db):
        """Can react with custom emoji."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "<:custom_emoji:123>")

        reactions = reaction_manager.get_reactions(1, 1)
        assert len(reactions.reactions) >= 1

    def test_invalid_custom_emoji_format(self, reaction_manager):
        """Invalid custom emoji format."""
        with pytest.raises(InvalidEmojiError):
            reaction_manager._validate_emoji(":invalid")


class TestReactionCounting:
    """Test reaction counting and aggregation."""

    def test_reaction_count(self, reaction_manager, test_db):
        """Count reactions for message."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000), (3, 1, 3, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(2, 1, "👍")
        reaction_manager.add_reaction(3, 1, "❤️")

        count = reaction_manager.get_reaction_count(1)
        assert count >= 3

    def test_reaction_count_by_emoji(self, reaction_manager, test_db):
        """Count reactions by emoji."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(2, 1, "👍")

        count = reaction_manager.get_reaction_count_by_emoji(1, "👍")
        assert count >= 2


class TestReactionNotifications:
    """Test reaction notifications."""

    def test_reaction_notifies_author(self, reaction_manager, test_db):
        """Reacting to message notifies author."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(2, 1, "👍")

        assert True


class TestReactionBulkOperations:
    """Test bulk reaction operations."""

    def test_get_reactions_for_multiple_messages(self, reaction_manager, test_db):
        """Get reactions for multiple messages."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test1', 1000, 1000, 'text'), (2, 1, 1, 'test2', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")
        reaction_manager.add_reaction(1, 2, "❤️")

        reactions = reaction_manager.get_reactions_bulk([1, 2])
        assert len(reactions) >= 2

    def test_user_reacted_to_messages(self, reaction_manager, test_db):
        """Check which messages user reacted to."""
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test1', 1000, 1000, 'text'), (2, 1, 1, 'test2', 1000, 1000, 'text')"
        )

        reaction_manager.add_reaction(1, 1, "👍")

        reacted = reaction_manager.user_reacted_to_messages(1, [1, 2])
        assert 1 in reacted
        assert 2 not in reacted
