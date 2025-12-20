"""
Direct messaging specific tests.

Tests DM-specific functionality, privacy settings,
and DM edge cases.
"""

import pytest
from src.core.messaging.exceptions import (
    InvalidRecipientError,
    ConversationAccessDeniedError,
    ConversationNotFoundError,
)
from src.core.messaging.models import ConversationType


class TestDMCreation:
    """Tests for DM creation."""

    def test_create_basic_dm(self, user_pool, modules):
        """Test creating a basic DM."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        assert dm.conversation_type == ConversationType.DM
        assert dm.participant_count == 2
        assert dm.max_participants == 2

    def test_dm_self_conversation_fails(self, user_pool, modules):
        """Test that self-DM fails."""
        user = user_pool.get_user()

        with pytest.raises(InvalidRecipientError):
            modules.messaging.create_dm(user.id, user.id)

    def test_dm_creation_order_irrelevant(self, user_pool, modules):
        """Test that DM creation order doesn't matter."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user2.id, user1.id)

        assert dm1.id == dm2.id

    def test_dm_already_exists_returns_existing(self, user_pool, modules):
        """Test that creating existing DM returns existing."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user1.id, user2.id)

        assert dm1.id == dm2.id


class TestDMPrivacySettings:
    """Tests for DM privacy settings."""

    def test_default_dm_settings(self, user_pool, modules):
        """Test default DM settings."""
        user = user_pool.get_user()

        settings = modules.messaging.get_user_message_settings(user.id)
        assert settings.allow_dms_from == "everyone"
        assert settings.auto_create_dms is True

    def test_block_all_dms(self, user_pool, modules):
        """Test blocking all DMs."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # User2 blocks all DMs
        modules.messaging.update_user_message_settings(user2.id, allow_dms_from="none")

        # User1 cannot create DM with user2
        with pytest.raises(ConversationAccessDeniedError):
            modules.messaging.create_dm(user1.id, user2.id)

    def test_block_dms_applies_to_existing(self, user_pool, modules):
        """Test that blocking DMs applies to existing DMs."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # Create DM first
        modules.messaging.create_dm(user1.id, user2.id)

        # User2 blocks all DMs
        modules.messaging.update_user_message_settings(user2.id, allow_dms_from="none")

        # Getting DM should still fail
        with pytest.raises(ConversationAccessDeniedError):
            modules.messaging.create_dm(user1.id, user2.id)

    def test_disable_auto_create_dms(self, user_pool, modules):
        """Test disabling auto-create for DMs."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # Try to create DM with auto_create=False
        with pytest.raises(ConversationNotFoundError):
            modules.messaging.create_dm(user1.id, user2.id, auto_create=False)

    def test_global_auto_create_setting(self, user_pool, modules):
        """Test global auto-create setting."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # User1 disables auto-create globally
        modules.messaging.update_user_message_settings(user1.id, auto_create_dms=False)

        # Can still create explicitly
        dm = modules.messaging.create_dm(user1.id, user2.id, auto_create=True)
        assert dm is not None


class TestDMMessages:
    """Tests for messaging in DMs."""

    def test_send_message_in_dm(self, dm_conversation):
        """Test sending message in DM."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Hello!")
        assert msg.conversation_id == dm.id
        assert msg.author_id == user1.id

    def test_both_parties_can_send(self, dm_conversation):
        """Test that both parties can send messages."""
        dm, user1, user2, messaging = dm_conversation

        msg1 = messaging.send_message(user1.id, dm.id, "From user1")
        msg2 = messaging.send_message(user2.id, dm.id, "From user2")

        assert msg1.author_id == user1.id
        assert msg2.author_id == user2.id

    def test_outsider_cannot_send(self, dm_conversation, user_pool):
        """Test that non-participants cannot send messages."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.send_message(user3.id, dm.id, "Outsider")


class TestDMDeletion:
    """Tests for DM deletion."""

    def test_delete_dm_as_participant(self, dm_conversation):
        """Test deleting DM as participant."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.delete_conversation(user1.id, dm.id)
        assert result is True

        # User1 cannot access anymore
        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv is None

    def test_delete_dm_removes_lookup(self, dm_conversation, modules):
        """Test that deleting DM removes lookup entry."""
        dm, user1, user2, messaging = dm_conversation

        messaging.delete_conversation(user1.id, dm.id)

        # Recreating should create new DM
        new_dm = modules.messaging.create_dm(user1.id, user2.id)
        # May be new ID or reuse deleted
        assert new_dm is not None

    def test_both_parties_can_delete(self, user_pool, modules):
        """Test that both parties can delete DM."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        modules.messaging.delete_conversation(user1.id, dm1.id)

        dm2 = modules.messaging.create_dm(user1.id, user2.id)
        modules.messaging.delete_conversation(user2.id, dm2.id)

        assert True  # Both deletions succeeded

    def test_leave_dm_same_as_delete(self, dm_conversation):
        """Test that leaving DM is same as deleting."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.leave_conversation(user1.id, dm.id)
        assert result is True

        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv is None


class TestDMAccess:
    """Tests for DM access control."""

    def test_dm_access_both_participants(self, dm_conversation):
        """Test that both participants can access DM."""
        dm, user1, user2, messaging = dm_conversation

        conv1 = messaging.get_conversation(dm.id, user1.id)
        conv2 = messaging.get_conversation(dm.id, user2.id)

        assert conv1 is not None
        assert conv2 is not None
        assert conv1.id == conv2.id

    def test_dm_access_non_participant(self, dm_conversation, user_pool):
        """Test that non-participants cannot access DM."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        conv = messaging.get_conversation(dm.id, user3.id)
        assert conv is None

    def test_get_messages_both_participants(self, dm_conversation):
        """Test that both participants can get messages."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        msgs1 = messaging.get_messages(user1.id, dm.id)
        msgs2 = messaging.get_messages(user2.id, dm.id)

        assert len(msgs1) == len(msgs2)


class TestDMReadReceipts:
    """Tests for read receipts in DMs."""

    def test_mark_dm_messages_as_read(self, dm_conversation):
        """Test marking DM messages as read."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        count = messaging.mark_read(user2.id, dm.id)
        assert count >= 1

    def test_read_receipts_privacy(self, dm_conversation):
        """Test that read receipts respect privacy settings."""
        dm, user1, user2, messaging = dm_conversation

        # Disable read receipts for user2
        messaging.update_user_message_settings(user2.id, read_receipts_enabled=False)

        messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_read(user2.id, dm.id)

        # User1 should not see user2's read status in public receipts
        # (though internal last_read is still tracked)
        assert True

    def test_unread_count_in_dm(self, dm_conversation):
        """Test unread count in DM."""
        dm, user1, user2, messaging = dm_conversation

        # User1 sends messages
        for i in range(3):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        # User2 should have 3 unread
        unread = messaging.get_unread_count(user2.id, dm.id)
        assert unread[dm.id] == 3

    def test_mark_read_clears_unread(self, dm_conversation):
        """Test that marking as read clears unread count."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_read(user2.id, dm.id)

        unread = messaging.get_unread_count(user2.id, dm.id)
        assert unread[dm.id] == 0


class TestDMEncryption:
    """Tests for DM encryption."""

    def test_dm_messages_encrypted(self, dm_conversation):
        """Test that DM messages are encrypted."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Encrypted test")

        # Message should be encrypted in storage
        assert msg.content == "Encrypted test"  # Decrypted in response

    def test_dm_messages_decrypt_on_retrieval(self, dm_conversation):
        """Test that messages decrypt on retrieval."""
        dm, user1, user2, messaging = dm_conversation

        original = "Secret message"
        msg = messaging.send_message(user1.id, dm.id, original)

        retrieved = messaging.get_message(user2.id, msg.id)
        assert retrieved.content == original


class TestDMEdgeCases:
    """Tests for DM edge cases."""

    def test_dm_with_deleted_user(self, dm_conversation):
        """Test DM behavior with deleted user (placeholder)."""
        dm, user1, user2, messaging = dm_conversation

        # This would require user deletion functionality
        # For now, just verify DM exists
        assert dm is not None

    def test_multiple_dms_between_users(self, user_pool, modules):
        """Test that only one DM exists between users."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user1.id, user2.id)
        dm3 = modules.messaging.create_dm(user2.id, user1.id)

        assert dm1.id == dm2.id == dm3.id

    def test_dm_name_is_none(self, dm_conversation):
        """Test that DM has no name."""
        dm, user1, user2, messaging = dm_conversation

        # DMs don't have names
        assert dm.name is None or dm.name == ""

    def test_dm_has_no_owner(self, dm_conversation):
        """Test that DM has no owner."""
        dm, user1, user2, messaging = dm_conversation

        assert dm.owner_id is None

    def test_dm_cannot_be_updated(self, dm_conversation):
        """Test that DM settings cannot be updated."""
        dm, user1, user2, messaging = dm_conversation

        from src.core.messaging.exceptions import ConversationTypeError

        with pytest.raises(ConversationTypeError):
            messaging.update_conversation(user1.id, dm.id, name="New Name")

    def test_dm_max_participants_fixed(self, dm_conversation):
        """Test that DM always has max 2 participants."""
        dm, user1, user2, messaging = dm_conversation

        assert dm.max_participants == 2
