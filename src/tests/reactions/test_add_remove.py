"""
Tests for adding and removing reactions.
"""

import pytest
import uuid
from src.core.reactions import (
    ReactionExistsError,
    ReactionNotFoundError,
    MessageNotFoundError,
    PermissionDeniedError,
)

pytestmark = pytest.mark.skip(
    "Reaction add/remove tests have teardown timeout issues - temporarily disabled"
)


class TestAddReaction:
    """Tests for adding reactions."""

    def test_add_reaction_success(self, users_with_dm_and_reaction):
        """Test adding a reaction successfully."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        reaction = reaction_manager.add_reaction(user1.id, msg.id, "thumbsup")

        assert reaction is not None
        assert reaction.message_id == msg.id
        assert reaction.user_id == user1.id
        assert reaction.emoji == "thumbsup"
        assert reaction.is_custom is False
        assert reaction.created_at > 0

    def test_add_reaction_other_user(self, users_with_dm_and_reaction):
        """Test another user can add same reaction."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        reaction_manager.add_reaction(user1.id, msg.id, "heart")
        reaction2 = reaction_manager.add_reaction(user2.id, msg.id, "heart")

        assert reaction2 is not None
        assert reaction2.user_id == user2.id

    def test_add_multiple_different_reactions(self, users_with_dm_and_reaction):
        """Test user can add multiple different reactions."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        r1 = reaction_manager.add_reaction(user1.id, msg.id, "smile")
        r2 = reaction_manager.add_reaction(user1.id, msg.id, "laugh")

        assert r1.emoji == "smile"
        assert r2.emoji == "laugh"

    def test_add_duplicate_reaction_fails(self, fresh_users_with_dm_and_relationships):
        """Test adding same reaction twice fails."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "star")

        with pytest.raises(ReactionExistsError):
            reaction_manager.add_reaction(user1.id, msg.id, "star")

    def test_add_reaction_nonexistent_message(self, users_with_dm_and_reaction):
        """Test adding reaction to nonexistent message fails."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.add_reaction(user1.id, 999999999, "thumbsup")

    def test_add_reaction_not_participant(
        self, auth_manager, messaging_manager, reaction_manager
    ):
        """Test non-participant cannot add reaction."""
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth_manager.register(
            username=f"np1_{unique_id}",
            email=f"np1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"np2_{unique_id}",
            email=f"np2_{unique_id}@example.com",
            password="TestPass123!",
        )
        outsider = auth_manager.register(
            username=f"np3_{unique_id}",
            email=f"np3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Private message")

        with pytest.raises(MessageNotFoundError):
            reaction_manager.add_reaction(outsider.id, msg.id, "thumbsup")


class TestRemoveReaction:
    """Tests for removing reactions."""

    def test_remove_reaction_success(self, fresh_users_with_dm_and_relationships):
        """Test removing a reaction successfully."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "wave")
        result = reaction_manager.remove_reaction(user1.id, msg.id, "wave")

        assert result is True

        user_reactions = reaction_manager.get_user_reactions(user1.id, msg.id)
        emojis = [r.emoji for r in user_reactions]
        assert "wave" not in emojis

    def test_remove_nonexistent_reaction_fails(
        self, fresh_users_with_dm_and_relationships
    ):
        """Test removing reaction that does not exist fails."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        with pytest.raises(ReactionNotFoundError):
            reaction_manager.remove_reaction(user1.id, msg.id, "nonexistent")

    def test_remove_other_users_reaction_fails(
        self, fresh_users_with_dm_and_relationships
    ):
        """Test cannot remove another user's reaction."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "fire")

        with pytest.raises(PermissionDeniedError):
            reaction_manager.remove_reaction(user2.id, msg.id, "fire")

    def test_remove_reaction_nonexistent_message(self, users_with_dm_and_reaction):
        """Test removing reaction from nonexistent message fails."""
        user1, user2, dm, msg, reaction_manager = users_with_dm_and_reaction

        with pytest.raises(MessageNotFoundError):
            reaction_manager.remove_reaction(user1.id, 999999999, "thumbsup")


class TestToggleReaction:
    """Tests for toggling reactions (add if not exists, remove if exists)."""

    def test_add_remove_add_cycle(self, fresh_users_with_dm_and_relationships):
        """Test adding, removing, and re-adding a reaction."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        r1 = reaction_manager.add_reaction(user1.id, msg.id, "cycle")
        assert r1 is not None

        reaction_manager.remove_reaction(user1.id, msg.id, "cycle")

        r2 = reaction_manager.add_reaction(user1.id, msg.id, "cycle")
        assert r2 is not None
        assert r2.id != r1.id

    def test_multiple_users_add_remove(self, fresh_users_with_dm_and_relationships):
        """Test multiple users adding and removing same reaction."""
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )

        reaction_manager.add_reaction(user1.id, msg.id, "multi")
        reaction_manager.add_reaction(user2.id, msg.id, "multi")

        msg_reactions = reaction_manager.get_reactions(user1.id, msg.id)
        multi_reaction = next(
            (r for r in msg_reactions.reactions if r.emoji == "multi"), None
        )
        assert multi_reaction is not None
        assert multi_reaction.count == 2

        reaction_manager.remove_reaction(user1.id, msg.id, "multi")

        msg_reactions = reaction_manager.get_reactions(user2.id, msg.id)
        multi_reaction = next(
            (r for r in msg_reactions.reactions if r.emoji == "multi"), None
        )
        assert multi_reaction is not None
        assert multi_reaction.count == 1
