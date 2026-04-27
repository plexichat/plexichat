"""Tests for messaging DM functionality."""

from unittest.mock import patch


class TestDM:
    """Test direct message functionality."""

    def test_create_dm(self, db, auth_manager, messaging_manager):
        """Test creating a DM conversation."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM between users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        assert dm is not None
        assert dm.id is not None
        # DM conversations have conversation_type attribute
        assert dm.conversation_type.value == "dm"

    def test_send_dm_message(self, db, auth_manager, messaging_manager):
        """Test sending a message in DM."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Send message
        message = messaging_manager.send_message(user1.id, dm.id, "Hello, user2!")

        assert message is not None
        assert message.id is not None
        assert message.content == "Hello, user2!"
        assert message.author_id == user1.id
        assert message.conversation_id == dm.id

    def test_get_dm_messages(self, db, auth_manager, messaging_manager):
        """Test retrieving messages from DM."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Send multiple messages
        msg1 = messaging_manager.send_message(user1.id, dm.id, "First message")
        msg2 = messaging_manager.send_message(user2.id, dm.id, "Second message")
        msg3 = messaging_manager.send_message(user1.id, dm.id, "Third message")

        # Retrieve messages
        messages = messaging_manager.get_messages(user1.id, dm.id, limit=10)

        assert messages is not None
        assert len(messages) >= 3
        message_ids = [m.id for m in messages]
        assert msg1.id in message_ids
        assert msg2.id in message_ids
        assert msg3.id in message_ids
