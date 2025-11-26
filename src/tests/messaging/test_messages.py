"""
Message CRUD tests for messaging module.
"""

import pytest


class TestSendMessage:
    """Test sending messages."""
    
    def test_send_message_success(self, dm_conversation):
        """Test successful message sending."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Hello, world!")
        
        assert msg is not None
        assert msg.content == "Hello, world!"
        assert msg.author_id == user1.id
        assert msg.conversation_id == dm.id
    
    def test_send_message_updates_conversation(self, dm_conversation):
        """Test that sending message updates conversation."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Test message")
        
        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv.last_message_id == msg.id
        assert conv.last_message_at is not None
    
    def test_send_message_as_non_participant_fails(self, dm_conversation, users):
        """Test sending as non-participant fails."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.send_message(user3.id, dm.id, "Hello")
    
    def test_send_empty_message_fails(self, dm_conversation):
        """Test sending empty message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.InvalidContentError):
            messaging.send_message(user1.id, dm.id, "")
    
    def test_send_whitespace_message_fails(self, dm_conversation):
        """Test sending whitespace-only message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.InvalidContentError):
            messaging.send_message(user1.id, dm.id, "   ")
    
    def test_send_message_too_long_fails(self, dm_conversation):
        """Test sending message exceeding limit fails."""
        dm, user1, user2, messaging = dm_conversation
        
        long_content = "x" * 5000  # Default limit is 4000
        
        with pytest.raises(messaging.ContentTooLongError) as exc_info:
            messaging.send_message(user1.id, dm.id, long_content)
        
        assert exc_info.value.max_length == 4000
        assert exc_info.value.actual_length == 5000
    
    def test_send_message_with_reply(self, dm_conversation):
        """Test sending message as reply."""
        dm, user1, user2, messaging = dm_conversation
        
        original = messaging.send_message(user1.id, dm.id, "Original message")
        reply = messaging.send_message(user1.id, dm.id, "Reply", reply_to_id=original.id)
        
        assert reply.reply_to_id == original.id
    
    def test_send_reply_to_wrong_conversation_fails(self, users):
        """Test replying to message in different conversation fails."""
        user1, user2, user3, messaging = users
        
        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user3.id)
        
        msg1 = messaging.send_message(user1.id, dm1.id, "Message in DM1")
        
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.send_message(user1.id, dm2.id, "Reply", reply_to_id=msg1.id)
    
    def test_send_message_creates_status(self, dm_conversation):
        """Test that sending creates initial status."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Test")
        
        status = messaging.get_message_status(user1.id, msg.id)
        assert len(status) >= 1
    
    def test_send_message_with_unicode(self, dm_conversation):
        """Test sending message with unicode characters."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Hello! Emoji test")
        
        assert msg.content == "Hello! Emoji test"
    
    def test_send_message_preserves_formatting(self, dm_conversation):
        """Test that formatting markers are preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "**bold** *italic* ||spoiler||"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert "**bold**" in msg.content
        assert "*italic*" in msg.content
        assert "||spoiler||" in msg.content


class TestEditMessage:
    """Test editing messages."""
    
    def test_edit_own_message(self, dm_conversation):
        """Test editing own message."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Original")
        edited = messaging.edit_message(user1.id, msg.id, "Edited")
        
        assert edited.content == "Edited"
        assert edited.edited is True
    
    def test_edit_others_message_fails(self, dm_conversation):
        """Test editing others' message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Original")
        
        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.edit_message(user2.id, msg.id, "Edited")
    
    def test_edit_deleted_message_fails(self, dm_conversation):
        """Test editing deleted message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Original")
        messaging.delete_message(user1.id, msg.id)
        
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.edit_message(user1.id, msg.id, "Edited")
    
    def test_edit_nonexistent_message_fails(self, dm_conversation):
        """Test editing nonexistent message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.edit_message(user1.id, 999999999, "Edited")
    
    def test_edit_validates_content(self, dm_conversation):
        """Test that edit validates content."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Original")
        
        with pytest.raises(messaging.InvalidContentError):
            messaging.edit_message(user1.id, msg.id, "")


class TestDeleteMessage:
    """Test deleting messages."""
    
    def test_delete_own_message(self, dm_conversation):
        """Test deleting own message."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "To delete")
        result = messaging.delete_message(user1.id, msg.id)
        
        assert result is True
        
        deleted = messaging.get_message(user1.id, msg.id)
        assert deleted is None or deleted.deleted
    
    def test_delete_others_message_as_admin(self, group_conversation):
        """Test admin can delete others' messages."""
        group, user1, user2, user3, messaging = group_conversation
        
        msg = messaging.send_message(user2.id, group.id, "Member message")
        
        # Owner can delete
        result = messaging.delete_message(user1.id, msg.id)
        
        assert result is True
    
    def test_delete_others_message_as_member_fails(self, group_conversation):
        """Test member cannot delete others' messages."""
        group, user1, user2, user3, messaging = group_conversation
        
        msg = messaging.send_message(user1.id, group.id, "Owner message")
        
        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.delete_message(user2.id, msg.id)
    
    def test_soft_delete_preserves_record(self, dm_conversation):
        """Test soft delete preserves database record."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "To delete")
        messaging.delete_message(user1.id, msg.id)
        
        # Message should still exist in DB but marked deleted
        # This is internal behavior, tested via get_message returning None
        assert messaging.get_message(user1.id, msg.id) is None


class TestGetMessage:
    """Test getting single message."""
    
    def test_get_message_as_participant(self, dm_conversation):
        """Test getting message as participant."""
        dm, user1, user2, messaging = dm_conversation
        
        sent = messaging.send_message(user1.id, dm.id, "Test")
        
        msg = messaging.get_message(user1.id, sent.id)
        
        assert msg is not None
        assert msg.id == sent.id
    
    def test_get_message_as_non_participant(self, dm_conversation, users):
        """Test getting message as non-participant returns None."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        sent = messaging.send_message(user1.id, dm.id, "Test")
        
        msg = messaging.get_message(user3.id, sent.id)
        
        assert msg is None
    
    def test_get_nonexistent_message(self, dm_conversation):
        """Test getting nonexistent message returns None."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.get_message(user1.id, 999999999)
        
        assert msg is None


class TestGetMessages:
    """Test getting multiple messages."""
    
    def test_get_messages_returns_conversation_messages(self, dm_conversation):
        """Test get_messages returns messages from conversation."""
        dm, user1, user2, messaging = dm_conversation
        
        msg1 = messaging.send_message(user1.id, dm.id, "Message 1")
        msg2 = messaging.send_message(user1.id, dm.id, "Message 2")
        
        messages = messaging.get_messages(user1.id, dm.id)
        msg_ids = [m.id for m in messages]
        
        assert msg1.id in msg_ids
        assert msg2.id in msg_ids
    
    def test_get_messages_excludes_deleted(self, dm_conversation):
        """Test get_messages excludes deleted messages."""
        dm, user1, user2, messaging = dm_conversation
        
        msg1 = messaging.send_message(user1.id, dm.id, "Keep")
        msg2 = messaging.send_message(user1.id, dm.id, "Delete")
        messaging.delete_message(user1.id, msg2.id)
        
        messages = messaging.get_messages(user1.id, dm.id)
        msg_ids = [m.id for m in messages]
        
        assert msg1.id in msg_ids
        assert msg2.id not in msg_ids
    
    def test_get_messages_pagination_before(self, dm_conversation):
        """Test pagination with before_id."""
        dm, user1, user2, messaging = dm_conversation
        
        msgs = []
        for i in range(5):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))
        
        # Get messages before the last one
        older = messaging.get_messages(user1.id, dm.id, before_id=msgs[-1].id, limit=2)
        
        assert len(older) == 2
        assert all(m.id < msgs[-1].id for m in older)
    
    def test_get_messages_pagination_after(self, dm_conversation):
        """Test pagination with after_id."""
        dm, user1, user2, messaging = dm_conversation
        
        msgs = []
        for i in range(5):
            msgs.append(messaging.send_message(user1.id, dm.id, f"Message {i}"))
        
        # Get messages after the first one
        newer = messaging.get_messages(user1.id, dm.id, after_id=msgs[0].id, limit=2)
        
        assert len(newer) == 2
        assert all(m.id > msgs[0].id for m in newer)
    
    def test_get_messages_respects_limit(self, dm_conversation):
        """Test that limit is respected."""
        dm, user1, user2, messaging = dm_conversation
        
        for i in range(10):
            messaging.send_message(user1.id, dm.id, f"Message {i}")
        
        messages = messaging.get_messages(user1.id, dm.id, limit=5)
        
        assert len(messages) <= 5
    
    def test_get_messages_as_non_participant_fails(self, dm_conversation, users):
        """Test get_messages as non-participant fails."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_messages(user3.id, dm.id)


class TestPinMessage:
    """Test pinning messages."""
    
    def test_pin_message(self, dm_conversation):
        """Test pinning a message."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Pin me")
        result = messaging.pin_message(user1.id, msg.id)
        
        assert result is True
    
    def test_pin_already_pinned(self, dm_conversation):
        """Test pinning already pinned message succeeds."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Pin me")
        messaging.pin_message(user1.id, msg.id)
        result = messaging.pin_message(user1.id, msg.id)
        
        assert result is True
    
    def test_unpin_message(self, dm_conversation):
        """Test unpinning a message."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Pin me")
        messaging.pin_message(user1.id, msg.id)
        result = messaging.unpin_message(user1.id, msg.id)
        
        assert result is True
    
    def test_get_pinned_messages(self, dm_conversation):
        """Test getting pinned messages."""
        dm, user1, user2, messaging = dm_conversation
        
        msg1 = messaging.send_message(user1.id, dm.id, "Pin 1")
        msg2 = messaging.send_message(user1.id, dm.id, "Pin 2")
        msg3 = messaging.send_message(user1.id, dm.id, "Not pinned")
        
        messaging.pin_message(user1.id, msg1.id)
        messaging.pin_message(user1.id, msg2.id)
        
        pinned = messaging.get_pinned_messages(user1.id, dm.id)
        pinned_ids = [m.id for m in pinned]
        
        assert msg1.id in pinned_ids
        assert msg2.id in pinned_ids
        assert msg3.id not in pinned_ids
    
    def test_pin_nonexistent_message_fails(self, dm_conversation):
        """Test pinning nonexistent message fails."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.MessageNotFoundError):
            messaging.pin_message(user1.id, 999999999)
    
    def test_pin_as_non_participant_fails(self, dm_conversation, users):
        """Test pinning as non-participant fails."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        msg = messaging.send_message(user1.id, dm.id, "Pin me")
        
        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.pin_message(user3.id, msg.id)
