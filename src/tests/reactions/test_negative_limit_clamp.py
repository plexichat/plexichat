"""Regression test: reaction-users listing ``limit`` clamped from below.

``get_reaction_users`` capped ``limit`` from above
(``min(limit, max_per_page)``) but never from below. A negative ``limit``
reached the SQL ``LIMIT ?`` verbatim, and SQLite treats ``LIMIT -1`` as
*unlimited* — so ``limit=-1`` dumped every reacting user instead of an
empty page.
"""

import uuid

import pytest


@pytest.mark.reactions
class TestReactionUsersNegativeLimitClamp:
    def test_get_reaction_users_negative_limit_returns_empty(
        self, auth_manager, messaging_manager, reaction_manager
    ):
        """A negative limit must not bypass the page cap (LIMIT -1 = all)."""
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth_manager.register(
            username=f"neg_lim1_{unique_id}",
            email=f"neg_lim1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"neg_lim2_{unique_id}",
            email=f"neg_lim2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Negative limit test")

        reaction_manager.add_reaction(user1.id, msg.id, ":neg_limit:")
        reaction_manager.add_reaction(user2.id, msg.id, ":neg_limit:")

        assert (
            reaction_manager.get_reaction_users(
                user1.id, msg.id, ":neg_limit:", limit=-1
            )
            == []
        )
        # A clamped negative limit behaves exactly like limit=0.
        assert (
            reaction_manager.get_reaction_users(
                user1.id, msg.id, ":neg_limit:", limit=0
            )
            == []
        )
        # Positive limits still work.
        assert (
            len(
                reaction_manager.get_reaction_users(
                    user1.id, msg.id, ":neg_limit:", limit=10
                )
            )
            == 2
        )
