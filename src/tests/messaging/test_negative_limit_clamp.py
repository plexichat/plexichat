"""Regression tests: messaging listing ``limit`` must be clamped from below.

``get_messages`` / ``search_messages`` / ``get_conversations`` capped
``limit`` from above (``min(limit, 100)``) but never from below. A negative
``limit`` reached the SQL ``LIMIT ?`` verbatim, and SQLite treats
``LIMIT -1`` as *unlimited* — so ``limit=-1`` dumped the whole conversation
/ search result / conversation list instead of an empty page.
"""

import pytest


@pytest.mark.messaging
class TestNegativeLimitClamp:
    def test_get_messages_negative_limit_returns_empty(
        self, messaging_manager, two_users
    ):
        """A negative limit must not bypass the page cap (LIMIT -1 = all)."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        for i in range(5):
            messaging_manager.send_message(user1.id, dm.id, f"Message {i}")
        assert messaging_manager.get_messages(user1.id, dm.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert messaging_manager.get_messages(user1.id, dm.id, limit=0) == []
        # Positive limits still work.
        assert len(messaging_manager.get_messages(user1.id, dm.id, limit=3)) == 3

    def test_search_messages_negative_limit_returns_empty(
        self, messaging_manager, two_users
    ):
        """Negative search limit must return [] instead of every match."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "find this keyword")
        messaging_manager.send_message(user1.id, dm.id, "unrelated message")
        assert (
            messaging_manager.search_messages(user1.id, dm.id, "keyword", limit=-1)
            == []
        )
        assert len(messaging_manager.search_messages(user1.id, dm.id, "keyword")) == 1

    def test_get_conversations_negative_limit_returns_empty(
        self, messaging_manager, two_users
    ):
        """Negative conversation-list limit must return [] instead of all."""
        user1, user2 = two_users
        messaging_manager.create_dm(user1.id, user2.id)
        assert messaging_manager.get_conversations(user1.id, limit=-1) == []
        assert len(messaging_manager.get_conversations(user1.id, limit=10)) == 1
