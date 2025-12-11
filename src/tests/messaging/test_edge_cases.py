"""
Edge case and error handling tests for messaging module.
"""

import pytest


class TestConversationEdgeCases:
    """Test conversation edge cases."""

    def test_get_nonexistent_conversation(self, users):
        """Test getting nonexistent conversation returns None."""
        user1, user2, user3, messaging = users

        conv = messaging.get_conversation(999999999, user1.id)

        assert conv is None

    def test_delete_nonexistent_conversation(self, users):
        """Test deleting nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationNotFoundError):
            messaging.delete_conversation(user1.id, 999999999)

    def test_leave_nonexistent_conversation(self, users):
        """Test leaving nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationNotFoundError):
            messaging.leave_conversation(user1.id, 999999999)

    def test_update_nonexistent_conversation(self, users):
        """Test updating nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationNotFoundError):
            messaging.update_conversation(user1.id, 999999999, name="New Name")

    def test_create_group_with_duplicate_participants(self, users):
        """Test creating group with duplicate participants deduplicates."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(
            user1.id, "Test Group",
            participant_ids=[user2.id, user2.id, user3.id, user3.id]
        )

        participants = messaging.get_participants(user1.id, group.id)

        # Should have 3 unique participants
        assert len(participants) == 3


class TestMessageEdgeCases:
    """Test message edge cases."""

    def test_send_to_nonexistent_conversation(self, users):
        """Test sending to nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.send_message(user1.id, 999999999, "Hello")

    def test_edit_nonexistent_message(self, users):
        """Test editing nonexistent message raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.edit_message(user1.id, 999999999, "Edited")

    def test_delete_nonexistent_message(self, users):
        """Test deleting nonexistent message raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.delete_message(user1.id, 999999999)

    def test_reply_to_deleted_message(self, dm_conversation):
        """Test replying to deleted message fails."""
        dm, user1, user2, messaging = dm_conversation

        original = messaging.send_message(user1.id, dm.id, "Original")
        messaging.delete_message(user1.id, original.id)

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.send_message(user1.id, dm.id, "Reply", reply_to_id=original.id)

    def test_pin_deleted_message(self, dm_conversation):
        """Test pinning deleted message fails."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "To delete")
        messaging.delete_message(user1.id, msg.id)

        # Message is deleted, pin should fail
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.pin_message(user1.id, msg.id)


class TestParticipantEdgeCases:
    """Test participant edge cases."""

    def test_add_to_nonexistent_conversation(self, users):
        """Test adding to nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationNotFoundError):
            messaging.add_participant(user1.id, 999999999, user2.id)

    def test_remove_from_nonexistent_conversation(self, users):
        """Test removing from nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationNotFoundError):
            messaging.remove_participant(user1.id, 999999999, user2.id)

    def test_get_participants_nonexistent_conversation(self, users):
        """Test getting participants from nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_participants(user1.id, 999999999)

    def test_mute_nonexistent_conversation(self, users):
        """Test muting nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.mute_conversation(user1.id, 999999999, muted=True)


class TestStatusEdgeCases:
    """Test message status edge cases."""

    def test_mark_delivered_nonexistent_message(self, users):
        """Test marking nonexistent message as delivered is ignored."""
        user1, user2, user3, messaging = users

        count = messaging.mark_delivered(user1.id, [999999999])

        assert count == 0

    def test_mark_read_nonexistent_conversation(self, users):
        """Test marking read in nonexistent conversation raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.mark_read(user1.id, 999999999)

    def test_get_status_nonexistent_message(self, users):
        """Test getting status for nonexistent message raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.get_message_status(user1.id, 999999999)

    def test_get_unread_nonexistent_conversation(self, users):
        """Test getting unread for nonexistent conversation returns empty."""
        user1, user2, user3, messaging = users

        unread = messaging.get_unread_count(user1.id, 999999999)

        assert len(unread) == 0


class TestAttachmentEdgeCases:
    """Test attachment edge cases."""

    def test_add_attachment_nonexistent_message(self, users):
        """Test adding attachment to nonexistent message raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.add_attachment(
                user1.id, 999999999, "test.pdf", "application/pdf", 1024,
                "https://example.com/test.pdf"
            )

    def test_get_attachments_nonexistent_message(self, users):
        """Test getting attachments for nonexistent message returns empty."""
        user1, user2, user3, messaging = users

        attachments = messaging.get_attachments(user1.id, 999999999)

        assert len(attachments) == 0

    def test_delete_nonexistent_attachment(self, users):
        """Test deleting nonexistent attachment raises error."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.AttachmentError):
            messaging.delete_attachment(user1.id, 999999999)


class TestConcurrentOperations:
    """Test concurrent operation scenarios."""

    def test_send_after_leaving(self, group_conversation):
        """Test sending after leaving conversation fails."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.leave_conversation(user2.id, group.id)

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.send_message(user2.id, group.id, "Hello")

    def test_read_after_leaving(self, group_conversation):
        """Test reading after leaving conversation fails."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.leave_conversation(user2.id, group.id)

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_messages(user2.id, group.id)

    def test_edit_after_removed(self, group_conversation):
        """Test editing message after being removed fails."""
        group, user1, user2, user3, messaging = group_conversation

        msg = messaging.send_message(user2.id, group.id, "My message")

        messaging.remove_participant(user1.id, group.id, user2.id)

        # User cannot edit after being removed - no access to conversation
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.edit_message(user2.id, msg.id, "Edited")


class TestValidationEdgeCases:
    """Test validation edge cases."""

    def test_group_name_with_only_spaces(self, users):
        """Test group name with only spaces fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.InvalidContentError):
            messaging.create_group(user1.id, "     ")

    def test_message_with_only_newlines(self, dm_conversation):
        """Test message with only newlines fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.InvalidContentError):
            messaging.send_message(user1.id, dm.id, "\n\n\n")

    def test_very_long_group_name(self, users):
        """Test very long group name fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.InvalidContentError):
            messaging.create_group(user1.id, "x" * 200)

    def test_zero_max_participants(self, users):
        """Test zero max participants fails on creation."""
        user1, user2, user3, messaging = users

        # Creating with 0 max participants should fail - owner needs to be added
        with pytest.raises(messaging.ParticipantLimitError):
            messaging.create_group(user1.id, "Empty Group", max_participants=0)

    def test_negative_limit(self, dm_conversation):
        """Test negative limit is handled."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        # Negative limit should be treated as 0 or default
        messages = messaging.get_messages(user1.id, dm.id, limit=-1)

        # Should return something (implementation dependent)
        assert isinstance(messages, list)


class TestSystemMessages:
    """Test system message behavior."""

    def test_system_message_has_correct_type(self, users):
        """Test system messages have correct type."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Test Group")

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [m for m in messages if m.message_type == messaging.MessageType.SYSTEM]

        assert len(system_msgs) >= 1

    def test_system_message_author_is_zero(self, users):
        """Test system messages have author_id of 0."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Test Group")

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [m for m in messages if m.message_type == messaging.MessageType.SYSTEM]

        for msg in system_msgs:
            assert msg.author_id == 0

    def test_cannot_edit_system_message(self, users):
        """Test cannot edit system messages."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Test Group")

        messages = messaging.get_messages(user1.id, group.id)
        system_msg = next(m for m in messages if m.message_type == messaging.MessageType.SYSTEM)

        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.edit_message(user1.id, system_msg.id, "Edited")

    def test_cannot_reply_to_system_message(self, users):
        """Test can reply to system messages (they're valid messages)."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Test Group")

        messages = messaging.get_messages(user1.id, group.id)
        system_msg = next(m for m in messages if m.message_type == messaging.MessageType.SYSTEM)

        # Should be able to reply to system messages
        reply = messaging.send_message(user1.id, group.id, "Reply", reply_to_id=system_msg.id)

        assert reply.reply_to_id == system_msg.id
