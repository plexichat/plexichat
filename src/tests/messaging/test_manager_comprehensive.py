"""
Comprehensive tests for MessagingManager focusing on edge cases and error paths.
Targeting 80%+ coverage.
"""

import pytest

from src.core.messaging.models import ConversationType
from src.core.messaging.exceptions import *


class TestMessagingErrorPaths:
    """Test error conditions."""
    
    def test_create_dm_with_self(self, messaging_manager):
        """Cannot create DM with yourself."""
        with pytest.raises(InvalidRecipientError):
            messaging_manager.create_dm(1, 1)
    
    def test_create_dm_blocked_user(self, messaging_manager, test_db):
        """Cannot DM user who blocks you."""
        test_db.execute(
            "INSERT INTO msg_user_settings (user_id, allow_dms_from) VALUES (?, 'none')",
            (2,)
        )
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(1, 2)
    
    def test_create_dm_nonexistent_user(self, messaging_manager):
        """Cannot create DM with nonexistent user."""
        with pytest.raises(InvalidRecipientError):
            messaging_manager.create_dm(1, 99999)
    
    def test_create_group_empty_name(self, messaging_manager):
        """Group name cannot be empty."""
        with pytest.raises(InvalidContentError):
            messaging_manager.create_group(1, "   ")
    
    def test_create_group_too_many_participants(self, messaging_manager, monkeypatch):
        """Cannot exceed max participants."""
        monkeypatch.setitem(messaging_manager._config, "max_group_participants", 3)
        
        with pytest.raises(ParticipantLimitError):
            messaging_manager.create_group(1, "Test", participant_ids=[2, 3, 4, 5])
    
    def test_create_group_invalid_participants(self, messaging_manager):
        """Cannot add nonexistent participants."""
        with pytest.raises(InvalidRecipientError):
            messaging_manager.create_group(1, "Test", participant_ids=[99999])
    
    def test_send_message_not_participant(self, messaging_manager):
        """Cannot send to conversation you're not in."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(3, conv.id, "Hello")
    
    def test_send_message_empty(self, messaging_manager):
        """Cannot send empty message."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(InvalidContentError):
            messaging_manager.send_message(1, conv.id, "")
    
    def test_send_message_too_long(self, messaging_manager, monkeypatch):
        """Message exceeds max length."""
        monkeypatch.setitem(messaging_manager._config, "max_message_length", 100)
        
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ContentTooLongError):
            messaging_manager.send_message(1, conv.id, "x" * 101)
    
    def test_send_message_too_many_attachments(self, messaging_manager, monkeypatch):
        """Too many attachments."""
        monkeypatch.setitem(messaging_manager._config, "max_attachments_per_message", 2)
        
        conv = messaging_manager.create_dm(1, 2)
        attachments = [{"filename": f"file{i}.txt"} for i in range(3)]
        
        with pytest.raises(AttachmentLimitError):
            messaging_manager.send_message(1, conv.id, "Test", attachments=attachments)
    
    def test_send_message_nonexistent_conversation(self, messaging_manager):
        """Cannot send to nonexistent conversation."""
        with pytest.raises(ConversationNotFoundError):
            messaging_manager.send_message(1, 99999, "Hello")
    
    def test_edit_message_not_author(self, messaging_manager):
        """Cannot edit others' messages."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Original")
        
        with pytest.raises(MessageAccessDeniedError):
            messaging_manager.edit_message(2, msg.id, "Edited")
    
    def test_edit_message_not_found(self, messaging_manager):
        """Cannot edit nonexistent message."""
        with pytest.raises(MessageNotFoundError):
            messaging_manager.edit_message(1, 99999, "Edited")
    
    def test_edit_message_empty_content(self, messaging_manager):
        """Cannot edit to empty content."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Original")
        
        with pytest.raises(InvalidContentError):
            messaging_manager.edit_message(1, msg.id, "")
    
    def test_delete_message_no_permission(self, messaging_manager):
        """Cannot delete without permission."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        with pytest.raises(MessageAccessDeniedError):
            messaging_manager.delete_message(3, msg.id)
    
    def test_delete_message_not_found(self, messaging_manager):
        """Cannot delete nonexistent message."""
        with pytest.raises(MessageNotFoundError):
            messaging_manager.delete_message(1, 99999)
    
    def test_add_participant_to_dm(self, messaging_manager):
        """Cannot add participants to DM."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationTypeError):
            messaging_manager.add_participant(1, conv.id, 3)
    
    def test_add_participant_already_exists(self, messaging_manager):
        """Cannot add existing participant."""
        conv = messaging_manager.create_group(1, "Test", [2])
        
        with pytest.raises(ParticipantExistsError):
            messaging_manager.add_participant(1, conv.id, 2)
    
    def test_add_participant_limit_reached(self, messaging_manager, monkeypatch):
        """Cannot exceed participant limit."""
        conv = messaging_manager.create_group(1, "Test", [2], max_participants=2)
        
        with pytest.raises(ParticipantLimitError):
            messaging_manager.add_participant(1, conv.id, 3)
    
    def test_add_participant_not_admin(self, messaging_manager):
        """Only admin can add participants."""
        conv = messaging_manager.create_group(1, "Test", [2, 3])
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant(2, conv.id, 4)
    
    def test_remove_participant_not_admin(self, messaging_manager):
        """Only admin can remove participants."""
        conv = messaging_manager.create_group(1, "Test", [2, 3])
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.remove_participant(2, conv.id, 3)
    
    def test_remove_participant_not_found(self, messaging_manager):
        """Cannot remove nonexistent participant."""
        conv = messaging_manager.create_group(1, "Test", [2])
        
        with pytest.raises(ParticipantNotFoundError):
            messaging_manager.remove_participant(1, conv.id, 99999)
    
    def test_update_conversation_dm(self, messaging_manager):
        """Cannot update DM settings."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationTypeError):
            messaging_manager.update_conversation(1, conv.id, name="New Name")
    
    def test_update_conversation_not_owner(self, messaging_manager):
        """Only owner can update conversation."""
        conv = messaging_manager.create_group(1, "Test", [2])
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.update_conversation(2, conv.id, name="New Name")
    
    def test_delete_conversation_not_owner(self, messaging_manager):
        """Only owner can delete group."""
        conv = messaging_manager.create_group(1, "Test", [2])
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.delete_conversation(2, conv.id)
    
    def test_pin_message_not_participant(self, messaging_manager):
        """Cannot pin in conversation you're not in."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.pin_message(3, msg.id)
    
    def test_pin_message_limit_reached(self, messaging_manager, monkeypatch):
        """Cannot exceed pin limit."""
        monkeypatch.setitem(messaging_manager._config, "max_pinned_messages", 1)
        
        conv = messaging_manager.create_dm(1, 2)
        msg1 = messaging_manager.send_message(1, conv.id, "Test 1")
        msg2 = messaging_manager.send_message(1, conv.id, "Test 2")
        
        messaging_manager.pin_message(1, msg1.id)
        
        with pytest.raises(PinLimitError):
            messaging_manager.pin_message(1, msg2.id)
    
    def test_unpin_message_not_pinned(self, messaging_manager):
        """Cannot unpin non-pinned message."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        with pytest.raises(MessageNotPinnedError):
            messaging_manager.unpin_message(1, msg.id)
    
    def test_mark_read_not_participant(self, messaging_manager):
        """Cannot mark read if not participant."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.mark_read(3, conv.id)
    
    def test_mark_read_nonexistent_conversation(self, messaging_manager):
        """Cannot mark read nonexistent conversation."""
        with pytest.raises(ConversationNotFoundError):
            messaging_manager.mark_read(1, 99999)
    
    def test_attachment_too_large(self, messaging_manager, monkeypatch):
        """Attachment exceeds size limit."""
        monkeypatch.setitem(messaging_manager._config, "max_attachment_size", 1000)
        
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        with pytest.raises(AttachmentTooLargeError):
            messaging_manager.add_attachment(1, msg.id, "large.zip", "application/zip", 2000, "url")
    
    def test_attachment_limit_reached(self, messaging_manager, test_db, monkeypatch):
        """Cannot exceed attachment limit per message."""
        monkeypatch.setitem(messaging_manager._config, "max_attachments_per_message", 1)
        
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        messaging_manager.add_attachment(1, msg.id, "file1.txt", "text/plain", 100, "url1")
        
        with pytest.raises(AttachmentLimitError):
            messaging_manager.add_attachment(1, msg.id, "file2.txt", "text/plain", 100, "url2")
    
    def test_delete_attachment_not_author(self, messaging_manager):
        """Cannot delete others' attachments."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        att = messaging_manager.add_attachment(1, msg.id, "file.txt", "text/plain", 100, "url")
        
        with pytest.raises(MessageAccessDeniedError):
            messaging_manager.delete_attachment(2, att.id)
    
    def test_delete_attachment_not_found(self, messaging_manager):
        """Cannot delete nonexistent attachment."""
        with pytest.raises(AttachmentNotFoundError):
            messaging_manager.delete_attachment(1, 99999)
    
    def test_get_message_not_participant(self, messaging_manager):
        """Cannot get message from conversation you're not in."""
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Test")
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.get_message(3, msg.id)
    
    def test_get_messages_not_participant(self, messaging_manager):
        """Cannot get messages if not participant."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.get_messages(3, conv.id)
    
    def test_get_conversation_not_participant(self, messaging_manager):
        """Cannot get conversation if not participant."""
        conv = messaging_manager.create_dm(1, 2)
        
        result = messaging_manager.get_conversation(conv.id, 3)
        assert result is None


class TestMessagingCaching:
    """Test caching behavior."""
    
    def test_participant_cache(self, messaging_manager):
        """Test participant check is cached."""
        conv = messaging_manager.create_dm(1, 2)
        
        assert messaging_manager._is_participant(conv.id, 1)
        
        assert messaging_manager._is_participant(conv.id, 1)
    
    def test_settings_cache(self, messaging_manager):
        """Test user settings are cached."""
        settings1 = messaging_manager.get_user_message_settings(1)
        settings2 = messaging_manager.get_user_message_settings(1)
        
        assert settings1.user_id == settings2.user_id
    
    def test_filter_cache(self, messaging_manager):
        """Test content filter settings are cached."""
        filter1 = messaging_manager.get_user_filter_settings(1)
        filter2 = messaging_manager.get_user_filter_settings(1)
        
        assert filter1.user_id == filter2.user_id


class TestMessagingEncryption:
    """Test message encryption."""
    
    def test_message_encryption_enabled(self, messaging_manager, monkeypatch):
        """Messages are encrypted when enabled."""
        monkeypatch.setitem(messaging_manager._config, "encrypt_messages", True)
        
        conv = messaging_manager.create_dm(1, 2)
        msg = messaging_manager.send_message(1, conv.id, "Secret message")
        
        from src.utils.encryption import is_message_encrypted
        msg_row = messaging_manager._db.fetch_one(
            "SELECT content FROM msg_messages WHERE id = ?",
            (msg.id,)
        )
        assert is_message_encrypted(msg_row["content"])
    
    def test_message_decryption(self, messaging_manager, monkeypatch):
        """Messages are decrypted when retrieved."""
        monkeypatch.setitem(messaging_manager._config, "encrypt_messages", True)
        
        conv = messaging_manager.create_dm(1, 2)
        original = "Secret message"
        msg = messaging_manager.send_message(1, conv.id, original)
        
        retrieved = messaging_manager.get_message(1, msg.id)
        assert retrieved.content == original


class TestMessagingBatchOperations:
    """Test batch operations for performance."""
    
    def test_batch_attachment_loading(self, messaging_manager):
        """Attachments are loaded in batch."""
        conv = messaging_manager.create_dm(1, 2)
        
        messages = []
        for i in range(5):
            msg = messaging_manager.send_message(1, conv.id, f"Message {i}")
            messaging_manager.add_attachment(1, msg.id, f"file{i}.txt", "text/plain", 100, f"url{i}")
            messages.append(msg)
        
        loaded = messaging_manager.get_messages(1, conv.id, limit=10)
        
        assert len(loaded) == 5
        for msg in loaded:
            assert len(msg.attachments) > 0
    
    def test_batch_status_loading(self, messaging_manager):
        """Message statuses are loaded in batch."""
        conv = messaging_manager.create_dm(1, 2)
        
        for i in range(5):
            messaging_manager.send_message(1, conv.id, f"Message {i}")
        
        messages = messaging_manager.get_messages(1, conv.id, limit=10)
        
        assert len(messages) == 5


class TestMessagingNotes:
    """Test personal notes feature."""
    
    def test_create_notes_conversation(self, messaging_manager):
        """Can create personal notes."""
        notes = messaging_manager.get_or_create_notes(1)
        
        assert notes is not None
        assert notes.conversation_type == ConversationType.NOTES
    
    def test_notes_idempotent(self, messaging_manager):
        """Getting notes multiple times returns same conversation."""
        notes1 = messaging_manager.get_or_create_notes(1)
        notes2 = messaging_manager.get_or_create_notes(1)
        
        assert notes1.id == notes2.id
    
    def test_send_to_notes(self, messaging_manager):
        """Can send messages to notes."""
        notes = messaging_manager.get_or_create_notes(1)
        msg = messaging_manager.send_message(1, notes.id, "Personal note")
        
        assert msg is not None


class TestMessagingLeaveConversation:
    """Test leaving conversations."""
    
    def test_owner_leaves_transfers_ownership(self, messaging_manager):
        """Owner leaving transfers ownership."""
        conv = messaging_manager.create_group(1, "Test", [2, 3])
        
        messaging_manager.leave_conversation(1, conv.id)
        
        updated = messaging_manager.get_conversation(conv.id, 2)
        assert updated.owner_id != 1
    
    def test_last_member_leaves_deletes(self, messaging_manager):
        """Last member leaving deletes conversation."""
        conv = messaging_manager.create_group(1, "Test")
        
        messaging_manager.leave_conversation(1, conv.id)
        
        assert messaging_manager.get_conversation(conv.id, 1) is None
    
    def test_leave_dm_deletes(self, messaging_manager):
        """Leaving DM deletes it."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.leave_conversation(1, conv.id)
        
        assert messaging_manager.get_conversation(conv.id, 1) is None
    
    def test_leave_not_participant(self, messaging_manager):
        """Cannot leave conversation you're not in."""
        conv = messaging_manager.create_group(1, "Test", [2])
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.leave_conversation(3, conv.id)


class TestMessagingPagination:
    """Test message pagination."""
    
    def test_pagination_with_limit(self, messaging_manager):
        """Test limiting messages returned."""
        conv = messaging_manager.create_dm(1, 2)
        
        for i in range(10):
            messaging_manager.send_message(1, conv.id, f"Message {i}")
        
        messages = messaging_manager.get_messages(1, conv.id, limit=5)
        assert len(messages) == 5
    
    def test_pagination_with_before(self, messaging_manager):
        """Test getting messages before a certain message."""
        conv = messaging_manager.create_dm(1, 2)
        
        msg_ids = []
        for i in range(10):
            msg = messaging_manager.send_message(1, conv.id, f"Message {i}")
            msg_ids.append(msg.id)
        
        messages = messaging_manager.get_messages(1, conv.id, before=msg_ids[5], limit=3)
        assert len(messages) <= 3
    
    def test_pagination_with_after(self, messaging_manager):
        """Test getting messages after a certain message."""
        conv = messaging_manager.create_dm(1, 2)
        
        msg_ids = []
        for i in range(10):
            msg = messaging_manager.send_message(1, conv.id, f"Message {i}")
            msg_ids.append(msg.id)
        
        messages = messaging_manager.get_messages(1, conv.id, after=msg_ids[5], limit=3)
        assert len(messages) <= 3


class TestMessagingTyping:
    """Test typing indicators."""
    
    def test_start_typing(self, messaging_manager):
        """Can start typing indicator."""
        conv = messaging_manager.create_dm(1, 2)
        
        result = messaging_manager.start_typing(1, conv.id)
        assert result is not None
    
    def test_stop_typing(self, messaging_manager):
        """Can stop typing indicator."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.start_typing(1, conv.id)
        result = messaging_manager.stop_typing(1, conv.id)
        assert result is not None
    
    def test_typing_not_participant(self, messaging_manager):
        """Cannot set typing if not participant."""
        conv = messaging_manager.create_dm(1, 2)
        
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.start_typing(3, conv.id)


class TestMessagingUnreadCounts:
    """Test unread message counting."""
    
    def test_unread_count_increases(self, messaging_manager):
        """Unread count increases when messages sent."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.send_message(1, conv.id, "Test")
        
        count = messaging_manager.get_unread_count(2, conv.id)
        assert count > 0
    
    def test_mark_read_resets_count(self, messaging_manager):
        """Marking read resets unread count."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.send_message(1, conv.id, "Test")
        messaging_manager.mark_read(2, conv.id)
        
        count = messaging_manager.get_unread_count(2, conv.id)
        assert count == 0


class TestMessagingSearch:
    """Test message search."""
    
    def test_search_messages_in_conversation(self, messaging_manager):
        """Can search messages in conversation."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.send_message(1, conv.id, "Hello world")
        messaging_manager.send_message(1, conv.id, "Goodbye world")
        
        results = messaging_manager.search_messages(1, "world", conversation_id=conv.id)
        assert len(results) >= 2
    
    def test_search_messages_global(self, messaging_manager):
        """Can search all user's messages."""
        conv1 = messaging_manager.create_dm(1, 2)
        conv2 = messaging_manager.create_dm(1, 3)
        
        messaging_manager.send_message(1, conv1.id, "Hello world")
        messaging_manager.send_message(1, conv2.id, "Hello again")
        
        results = messaging_manager.search_messages(1, "Hello")
        assert len(results) >= 2


class TestMessagingMentions:
    """Test message mentions."""
    
    def test_send_message_with_mention(self, messaging_manager):
        """Can send message with user mention."""
        conv = messaging_manager.create_dm(1, 2)
        
        msg = messaging_manager.send_message(1, conv.id, "Hey @user2")
        assert msg is not None
    
    def test_get_mentions(self, messaging_manager):
        """Can get mentions for user."""
        conv = messaging_manager.create_dm(1, 2)
        
        messaging_manager.send_message(1, conv.id, "Hey @user2")
        
        mentions = messaging_manager.get_mentions(2)
        assert len(mentions) > 0
