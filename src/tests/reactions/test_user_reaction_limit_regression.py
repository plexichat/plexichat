"""Regression tests for the per-user distinct-reaction limit.

``max_unique_reactions_per_user`` caps how many *distinct* emoji a single
user may add to one message. The guard used to be short-circuited by a
query that asked "does ANY user already have this emoji on the message",
so a user sitting at their cap could keep adding emojis that other users
had already used -- the cap only held while the message was untouched by
anyone else.
"""

import uuid

import pytest

from src.core.reactions import ReactionLimitError


class TestPerUserDistinctReactionLimit:
    def test_cap_holds_when_another_user_already_used_the_emoji(
        self, auth_manager, messaging_manager, reaction_manager, monkeypatch
    ):
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"cap_a_{unique_id}",
            email=f"cap_a_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"cap_b_{unique_id}",
            email=f"cap_b_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Cap test")

        monkeypatch.setitem(
            reaction_manager._config, "max_unique_reactions_per_user", 1
        )

        # user1 spends their single slot.
        reaction_manager.add_reaction(user1.id, msg.id, "👍")

        # user2 puts the second emoji on the message first -- it is now an
        # *existing* emoji, so the message-level cap is not at risk.
        reaction_manager.add_reaction(user2.id, msg.id, "🎉")

        # user1 must still be refused: adding that emoji would take them to
        # 2 distinct emoji, over their cap of 1.
        with pytest.raises(ReactionLimitError):
            reaction_manager.add_reaction(user1.id, msg.id, "🎉")

        # The refused reaction must not have been persisted for user1 --
        # only user2's own reaction on that emoji should remain.
        assert [
            r.emoji for r in reaction_manager.get_user_reactions(user1.id, msg.id)
        ] == ["👍"]
        assert reaction_manager.get_reaction_count_by_emoji(msg.id, "🎉", user1.id) == 1

    def test_cap_holds_when_emoji_exists_from_another_user_only(
        self, auth_manager, messaging_manager, reaction_manager, monkeypatch
    ):
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"cap2_a_{unique_id}",
            email=f"cap2_a_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"cap2_b_{unique_id}",
            email=f"cap2_b_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Cap test 2")

        monkeypatch.setitem(
            reaction_manager._config, "max_unique_reactions_per_user", 3
        )

        # user2 seeds the message with several distinct emoji.
        for emoji in ("🎉", "😂", "❤️"):
            reaction_manager.add_reaction(user2.id, msg.id, emoji)

        # Tighten the per-user cap, then fill user1's two allowed slots.
        monkeypatch.setitem(
            reaction_manager._config, "max_unique_reactions_per_user", 2
        )
        reaction_manager.add_reaction(user1.id, msg.id, "👍")
        reaction_manager.add_reaction(user1.id, msg.id, "🔥")

        # A third distinct emoji for user1 must be refused even though
        # other users already used emoji that user1 has not.
        with pytest.raises(ReactionLimitError):
            reaction_manager.add_reaction(user1.id, msg.id, "😂")

        assert len(reaction_manager.get_user_reactions(user1.id, msg.id)) == 2

    def test_message_level_cap_still_allows_new_users_existing_emoji(
        self, auth_manager, messaging_manager, reaction_manager, monkeypatch
    ):
        """The message-level cap must keep allowing reactions to emoji
        already present on the message (only the per-user cap bites)."""
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"cap3_a_{unique_id}",
            email=f"cap3_a_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"cap3_b_{unique_id}",
            email=f"cap3_b_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Cap test 3")

        monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 2)

        reaction_manager.add_reaction(user1.id, msg.id, ":one:")
        reaction_manager.add_reaction(user1.id, msg.id, ":two:")

        # A brand-new distinct emoji exceeds the message cap.
        with pytest.raises(ReactionLimitError):
            reaction_manager.add_reaction(user2.id, msg.id, ":three:")

        # An already-present emoji is still fine for user2.
        reaction = reaction_manager.add_reaction(user2.id, msg.id, ":one:")
        assert reaction is not None
