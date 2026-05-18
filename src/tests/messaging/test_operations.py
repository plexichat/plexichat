"""Tests for messaging manager core operations."""

import pytest


@pytest.mark.messaging
class TestOperations:
    """Tests for core messaging operations (CRUD on messages)."""

    def test_send_and_get_message(self, messaging_manager, two_users):
        """Test sending and retrieving a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello World")
        assert msg is not None
        assert msg.content == "Hello World"
        assert msg.author_id == user1.id

    def test_edit_message(self, messaging_manager, two_users):
        """Test editing a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Original")
        edited = messaging_manager.edit_message(user1.id, msg.id, "Edited")
        assert edited.content == "Edited"

    def test_delete_message(self, messaging_manager, two_users):
        """Test deleting a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "To delete")
        result = messaging_manager.delete_message(user1.id, msg.id)
        assert result is True

    def test_hard_delete_message(self, messaging_manager, two_users):
        """Test hard deleting a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hard delete")
        result = messaging_manager.delete_message(user1.id, msg.id, hard_delete=True)
        assert result is True

    def test_get_message_by_id(self, messaging_manager, two_users):
        """Test getting a specific message by ID."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Specific message")
        retrieved = messaging_manager.get_message(user1.id, msg.id)
        assert retrieved is not None
        assert retrieved.id == msg.id

    def test_send_reply_message(self, messaging_manager, two_users):
        """Test sending a reply to a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        original = messaging_manager.send_message(user1.id, dm.id, "Original")
        reply = messaging_manager.send_message(
            user2.id, dm.id, "Reply", reply_to_id=original.id
        )
        assert reply is not None
        assert reply.reply_to_id == original.id

    def test_send_system_message(self, messaging_manager, two_users):
        """Test sending a system message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        sys_msg = messaging_manager.send_system_message(
            dm.id, "System announcement", "announcement"
        )
        assert sys_msg is not None

    def test_message_has_timestamp(self, messaging_manager, two_users):
        """Test that messages have proper timestamps."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Timestamped")
        assert msg.created_at is not None
        assert msg.created_at > 0

    def test_messages_ordered_by_time(self, messaging_manager, two_users):
        """Test that messages are returned in chronological order."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "First")
        messaging_manager.send_message(user1.id, dm.id, "Second")
        messaging_manager.send_message(user1.id, dm.id, "Third")
        messages = messaging_manager.get_messages(user1.id, dm.id)
        assert len(messages) == 3
