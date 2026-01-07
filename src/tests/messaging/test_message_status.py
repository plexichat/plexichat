"""
Message status (delivery/read receipts) tests for messaging module.
"""

import pytest


class TestMarkDelivered:
    """Test marking messages as delivered."""

    def test_mark_delivered_success(self, dm_conversation):
        """Test marking message as delivered."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        count = messaging.mark_delivered(user2.id, [msg.id])

        assert count == 1

    def test_mark_delivered_multiple(self, dm_conversation):
        """Test marking multiple messages as delivered."""
        dm, user1, user2, messaging = dm_conversation

        msg1 = messaging.send_message(user1.id, dm.id, "Test 1")
        msg2 = messaging.send_message(user1.id, dm.id, "Test 2")

        count = messaging.mark_delivered(user2.id, [msg1.id, msg2.id])

        assert count == 2

    def test_mark_own_message_delivered_ignored(self, dm_conversation):
        """Test that marking own message as delivered is ignored."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        count = messaging.mark_delivered(user1.id, [msg.id])

        assert count == 0

    def test_mark_delivered_non_participant_ignored(self, dm_conversation, users):
        """Test that non-participant cannot mark delivered."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        msg = messaging.send_message(user1.id, dm.id, "Test")

        count = messaging.mark_delivered(user3.id, [msg.id])

        assert count == 0

    def test_mark_already_delivered_ignored(self, dm_conversation):
        """Test that already delivered message is not counted again."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        messaging.mark_delivered(user2.id, [msg.id])
        count = messaging.mark_delivered(user2.id, [msg.id])

        assert count == 0

    def test_mark_delivered_updates_status(self, dm_conversation):
        """Test that mark_delivered updates message status."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_delivered(user2.id, [msg.id])

        status = messaging.get_message_status(user1.id, msg.id)
        delivered = [
            s for s in status if s.status == messaging.MessageStatusType.DELIVERED
        ]

        assert len(delivered) >= 1


class TestMarkRead:
    """Test marking messages as read."""

    def test_mark_read_all(self, fresh_dm):
        """Test marking all messages as read."""
        dm, user1, user2, messaging = fresh_dm

        messaging.send_message(user1.id, dm.id, "Test 1")
        messaging.send_message(user1.id, dm.id, "Test 2")

        count = messaging.mark_read(user2.id, dm.id)

        assert count == 2

    def test_mark_read_up_to_message(self, fresh_dm):
        """Test marking messages as read up to specific message."""
        dm, user1, user2, messaging = fresh_dm

        messaging.send_message(user1.id, dm.id, "Test 1")
        msg2 = messaging.send_message(user1.id, dm.id, "Test 2")
        messaging.send_message(user1.id, dm.id, "Test 3")

        count = messaging.mark_read(user2.id, dm.id, up_to_message_id=msg2.id)

        # Should mark msg1 and msg2 as read, not msg3
        assert count == 2

    def test_mark_read_updates_participant(self, dm_conversation):
        """Test that mark_read updates participant's last_read."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_read(user2.id, dm.id)

        participants = messaging.get_participants(user2.id, dm.id)
        user2_participant = next(p for p in participants if p.user_id == user2.id)

        assert user2_participant.last_read_message_id == msg.id

    def test_mark_read_non_participant_fails(self, dm_conversation, users):
        """Test that non-participant cannot mark read."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        messaging.send_message(user1.id, dm.id, "Test")

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.mark_read(user3.id, dm.id)

    def test_mark_read_excludes_own_messages(self, dm_conversation):
        """Test that own messages are not marked as read."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "From user1")
        messaging.send_message(user2.id, dm.id, "From user2")

        count = messaging.mark_read(user1.id, dm.id)

        # Only user2's message should be marked
        assert count == 1

    def test_mark_already_read_ignored(self, dm_conversation):
        """Test that already read messages are not counted again."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")

        messaging.mark_read(user2.id, dm.id)
        count = messaging.mark_read(user2.id, dm.id)

        assert count == 0


class TestGetUnreadCount:
    """Test getting unread message counts."""

    def test_get_unread_count_single_conversation(self, dm_conversation):
        """Test getting unread count for single conversation."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test 1")
        messaging.send_message(user1.id, dm.id, "Test 2")

        unread = messaging.get_unread_count(user2.id, dm.id)

        assert dm.id in unread
        assert unread[dm.id] == 2

    def test_get_unread_count_all_conversations(self, users):
        """Test getting unread count for all conversations."""
        user1, user2, user3, messaging = users

        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user3.id)

        messaging.send_message(user2.id, dm1.id, "From user2")
        messaging.send_message(user3.id, dm2.id, "From user3")

        unread = messaging.get_unread_count(user1.id)

        assert dm1.id in unread
        assert dm2.id in unread

    def test_get_unread_count_after_read(self, dm_conversation):
        """Test unread count decreases after marking read."""
        dm, user1, user2, messaging = dm_conversation

        msg1 = messaging.send_message(user1.id, dm.id, "Test 1")
        messaging.send_message(user1.id, dm.id, "Test 2")

        # Mark first as read
        messaging.mark_read(user2.id, dm.id, up_to_message_id=msg1.id)

        unread = messaging.get_unread_count(user2.id, dm.id)

        assert unread[dm.id] == 1

    def test_get_unread_count_excludes_own(self, fresh_dm):
        """Test unread count excludes own messages."""
        dm, user1, user2, messaging = fresh_dm

        messaging.send_message(user1.id, dm.id, "From user1")
        messaging.send_message(user2.id, dm.id, "From user2")

        unread = messaging.get_unread_count(user1.id, dm.id)

        # Only user2's message should be unread for user1
        assert unread[dm.id] == 1

    def test_get_unread_count_zero_when_all_read(self, dm_conversation):
        """Test unread count is zero when all read."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_read(user2.id, dm.id)

        unread = messaging.get_unread_count(user2.id, dm.id)

        assert unread[dm.id] == 0


class TestGetMessageStatus:
    """Test getting message status."""

    def test_get_status_as_sender(self, dm_conversation):
        """Test sender can get message status."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        status = messaging.get_message_status(user1.id, msg.id)

        assert len(status) >= 1

    def test_get_status_as_non_sender_fails(self, dm_conversation):
        """Test non-sender cannot get message status."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")

        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.get_message_status(user2.id, msg.id)

    def test_get_status_shows_delivered(self, dm_conversation):
        """Test status shows delivered."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_delivered(user2.id, [msg.id])

        status = messaging.get_message_status(user1.id, msg.id)
        delivered = [
            s for s in status if s.status == messaging.MessageStatusType.DELIVERED
        ]

        assert len(delivered) >= 1
        assert delivered[0].user_id == user2.id

    def test_get_status_shows_read(self, dm_conversation):
        """Test status shows read."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_read(user2.id, dm.id)

        status = messaging.get_message_status(user1.id, msg.id)
        read = [s for s in status if s.status == messaging.MessageStatusType.READ]

        assert len(read) >= 1
        assert read[0].user_id == user2.id

    def test_get_status_nonexistent_message_fails(self, dm_conversation):
        """Test getting status for nonexistent message fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.get_message_status(user1.id, 999999999)

    def test_status_includes_timestamp(self, dm_conversation):
        """Test status includes timestamp."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.mark_delivered(user2.id, [msg.id])

        status = messaging.get_message_status(user1.id, msg.id)

        assert all(s.timestamp > 0 for s in status)
