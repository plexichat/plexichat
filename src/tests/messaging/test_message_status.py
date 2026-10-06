"""Tests for messaging message status tracking."""

import pytest


@pytest.mark.messaging
class TestMessageStatus:
    """Tests for message delivery and read status tracking."""

    def test_mark_messages_delivered(self, messaging_manager, two_users):
        """Test marking messages as delivered."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello")
        count = messaging_manager.mark_delivered(user2.id, [msg.id])
        assert count >= 1

    def test_mark_messages_read(self, messaging_manager, two_users):
        """Test marking messages as read."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "Hello")
        count = messaging_manager.mark_read(user2.id, dm.id)
        assert count >= 1

    def test_get_unread_count(self, messaging_manager, two_users):
        """Test getting unread message count."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "Hello")
        unread = messaging_manager.get_unread_count(user2.id, dm.id)
        assert isinstance(unread, dict)

    def test_get_unread_count_all(self, messaging_manager, two_users):
        """Test getting unread count across all conversations."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "Hello")
        unread = messaging_manager.get_unread_count(user2.id)
        assert isinstance(unread, dict)

    def test_get_message_status(self, messaging_manager, two_users):
        """Test getting status for a specific message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello")
        statuses = messaging_manager.get_message_status(user1.id, msg.id)
        assert isinstance(statuses, list)

    def test_reader_ids(self, messaging_manager, two_users):
        """Test getting reader IDs for a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello")
        messaging_manager.mark_read(user2.id, dm.id)
        reader_ids = messaging_manager.get_reader_ids(user1.id, msg.id)
        assert isinstance(reader_ids, list)

    def test_mark_unread_rolls_back_to_previous_message(
        self, messaging_manager, two_users
    ):
        """mark_unread must land on the real predecessor, not max_id - 1."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg1 = messaging_manager.send_message(user1.id, dm.id, "One")
        msg2 = messaging_manager.send_message(user1.id, dm.id, "Two")
        # Snowflake IDs are sparse: max_id - 1 is not a real message.
        assert msg2.id - msg1.id != 1
        messaging_manager.mark_read(user2.id, dm.id)
        messaging_manager.mark_unread(user2.id, dm.id)
        last_read = messaging_manager.get_last_read(user2.id, dm.id)
        assert last_read["last_read_message_id"] == msg1.id

    def test_mark_unread_single_message_resets_to_zero(
        self, messaging_manager, two_users
    ):
        """With one message there is no predecessor, so last_read resets."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "Only")
        messaging_manager.mark_read(user2.id, dm.id)
        messaging_manager.mark_unread(user2.id, dm.id)
        last_read = messaging_manager.get_last_read(user2.id, dm.id)
        assert last_read["last_read_message_id"] in (0, None)
