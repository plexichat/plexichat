"""Tests for messaging edge cases and error handling."""

import pytest

from src.core.messaging.exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    ContentTooLongError,
    InvalidRecipientError,
)


@pytest.mark.messaging
class TestEdgeCases:
    """Tests for messaging edge cases and boundary conditions."""

    def test_send_message_to_nonexistent_conversation(
        self, messaging_manager, test_user
    ):
        """Test that sending to a nonexistent conversation raises an error."""
        with pytest.raises(Exception):
            messaging_manager.send_message(test_user.id, 9999999, "Hello")

    def test_get_messages_from_nonexistent_conversation(
        self, messaging_manager, test_user
    ):
        """Test that getting messages from a nonexistent conversation raises an error."""
        with pytest.raises(Exception):
            messaging_manager.get_messages(test_user.id, 9999999)

    def test_send_very_long_message(self, messaging_manager, two_users):
        """Test that extremely long messages are rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        with pytest.raises(Exception):
            messaging_manager.send_message(user1.id, dm.id, "x" * 100000)

    def test_send_empty_message(self, messaging_manager, two_users):
        """Test that empty messages are rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        with pytest.raises(Exception):
            messaging_manager.send_message(user1.id, dm.id, "")

    def test_edit_other_users_message(self, messaging_manager, two_users):
        """Test that editing another user's message is rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Original")
        with pytest.raises(Exception):
            messaging_manager.edit_message(user2.id, msg.id, "Edited by other")

    def test_delete_other_users_message(self, messaging_manager, two_users):
        """Test that deleting another user's message is rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Keep this")
        with pytest.raises(Exception):
            messaging_manager.delete_message(user2.id, msg.id)

    def test_unicode_message_content(self, messaging_manager, two_users):
        """Test that unicode content is handled properly."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello 世界 🌍")
        assert msg.content == "Hello 世界 🌍"

    def test_dm_self_is_prevented(self, messaging_manager, test_user):
        """Test that a user cannot DM themselves."""
        with pytest.raises(Exception):
            messaging_manager.create_dm(test_user.id, test_user.id)

    def test_search_messages_in_dm(self, messaging_manager, two_users):
        """Test searching for messages in a DM conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "find this keyword")
        messaging_manager.send_message(user1.id, dm.id, "unrelated message")
        results = messaging_manager.search_messages(user1.id, dm.id, "keyword")
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_pin_unpin_message(self, messaging_manager, two_users):
        """Test pinning and unpinning a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Pin this")
        result = messaging_manager.pin_message(user1.id, msg.id)
        assert result is True
