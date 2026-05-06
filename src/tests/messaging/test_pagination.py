"""Tests for messaging cursor-based pagination."""

import pytest


@pytest.mark.messaging
class TestPagination:
    """Tests for message and conversation pagination."""

    def test_get_messages_default_limit(self, messaging_manager, two_users):
        """Test getting messages with default limit."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        for i in range(5):
            messaging_manager.send_message(user1.id, dm.id, f"Message {i}")
        messages = messaging_manager.get_messages(user1.id, dm.id)
        assert len(messages) == 5

    def test_get_messages_with_limit(self, messaging_manager, two_users):
        """Test getting messages with a specific limit."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        for i in range(10):
            messaging_manager.send_message(user1.id, dm.id, f"Message {i}")
        messages = messaging_manager.get_messages(user1.id, dm.id, limit=3)
        assert len(messages) == 3

    def test_get_messages_with_before_cursor(self, messaging_manager, two_users):
        """Test getting messages before a specific message ID."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        for i in range(10):
            messaging_manager.send_message(user1.id, dm.id, f"Message {i}")
        all_msgs = messaging_manager.get_messages(user1.id, dm.id)
        if len(all_msgs) > 3:
            before_id = all_msgs[3].id
            older = messaging_manager.get_messages(user1.id, dm.id, before_id=before_id)
            for msg in older:
                assert msg.id < before_id

    def test_get_messages_with_after_cursor(self, messaging_manager, two_users):
        """Test getting messages after a specific message ID."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        for i in range(10):
            messaging_manager.send_message(user1.id, dm.id, f"Message {i}")
        all_msgs = messaging_manager.get_messages(user1.id, dm.id)
        if len(all_msgs) > 3:
            after_id = all_msgs[-3].id
            newer = messaging_manager.get_messages(user1.id, dm.id, after_id=after_id)
            for msg in newer:
                assert msg.id > after_id

    def test_get_conversations_pagination(self, messaging_manager, two_users):
        """Test getting conversations with pagination."""
        user1, user2 = two_users
        messaging_manager.create_dm(user1.id, user2.id)
        conversations = messaging_manager.get_conversations(user1.id, limit=10)
        assert len(conversations) >= 1

    def test_empty_conversation_returns_no_messages(self, messaging_manager, two_users):
        """Test that a new conversation has no messages."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messages = messaging_manager.get_messages(user1.id, dm.id)
        assert len(messages) == 0
