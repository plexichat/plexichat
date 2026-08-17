"""
Regression tests for reaction bugs fixed during the audit.

R1: get_reaction_users (route) did not URL-decode the emoji, so non-ASCII
    emoji lookups failed. Verified at the manager level (the route decodes
    before calling it).
R4: the block relationship was only enforced on reads; reacting to a message
    authored by (or authored by someone who blocked) the reactor was allowed.
"""

import pytest

from src.core.reactions.exceptions import UserBlockedError


class TestBlockEnforcedOnWrite:
    """R4: blocked relationships must block reaction writes."""

    def test_blocked_user_cannot_add_reaction(
        self, fresh_users_with_dm_and_relationships, messaging_manager
    ):
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )
        # user2 blocks user1; user1 must not be able to react to a message
        # authored by user2.
        rel_manager.block_user(user2.id, user1.id)
        authored = messaging_manager.send_message(
            user_id=user2.id, conversation_id=dm.id, content="from user2"
        )
        with pytest.raises(UserBlockedError):
            reaction_manager.add_reaction(user1.id, authored.id, "fire")

    def test_blocked_by_user_cannot_add_reaction(
        self, fresh_users_with_dm_and_relationships
    ):
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )
        # user1 blocks user2; user2 must not be able to react to user1's content.
        rel_manager.block_user(user1.id, user2.id)
        with pytest.raises(UserBlockedError):
            reaction_manager.add_reaction(user2.id, msg.id, "fire")

    def test_unblocked_user_can_add_reaction(
        self, fresh_users_with_dm_and_relationships
    ):
        user1, user2, dm, msg, reaction_manager, rel_manager = (
            fresh_users_with_dm_and_relationships
        )
        reaction = reaction_manager.add_reaction(user1.id, msg.id, "fire")
        assert reaction is not None
