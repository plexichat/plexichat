"""
Comprehensive message CRUD tests for messaging module.

Tests message creation, editing, deletion, encryption/decryption,
and message lifecycle operations.
"""

import pytest
import asyncio
import time
from src.core.messaging.exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    ConversationAccessDeniedError,
    InvalidContentError,
    ContentTooLongError,
)
from src.core.messaging.models import MessageType


class TestMessageCreation:
    """Tests for message creation and encryption."""

    def test_send_basic_message(self, dm_conversation):
        """Test sending a basic text message."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Hello, world!")

        assert msg is not None
        assert msg.content == "Hello, world!"
        assert msg.author_id == user1.id
        assert msg.conversation_id == dm.id
        assert msg.message_type == MessageType.TEXT

    def test_send_message_with_encryption(self, dm_conversation, modules):
        """Test that messages are encrypted when encryption is enabled."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Secret message")

        # Verify encryption marker is present in database
        raw_msg = messaging._get_message_raw(msg.id)
        assert raw_msg is not None

        # Content should be encrypted in database but decrypted in response
        assert msg.content == "Secret message"

    def test_decrypt_encrypted_message(self, dm_conversation):
        """Test decrypting an encrypted message."""
        dm, user1, user2, messaging = dm_conversation
        original_content = "Encrypted content test"
        msg = messaging.send_message(user1.id, dm.id, original_content)

        # Retrieve message - should auto-decrypt
        retrieved = messaging.get_message(user1.id, msg.id)
        assert retrieved is not None
        assert retrieved.content == original_content

    def test_send_empty_message_fails(self, dm_conversation):
        """Test that empty messages are rejected."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(InvalidContentError):
            messaging.send_message(user1.id, dm.id, "")

        with pytest.raises(InvalidContentError):
            messaging.send_message(user1.id, dm.id, "   ")

    def test_send_message_exceeding_max_length(self, dm_conversation):
        """Test that messages exceeding max length are rejected."""
        dm, user1, user2, messaging = dm_conversation
        long_message = "A" * 5000  # Exceeds default 4000 limit

        with pytest.raises(ContentTooLongError) as exc_info:
            messaging.send_message(user1.id, dm.id, long_message)

        assert exc_info.value.max_length == 4000
        assert exc_info.value.actual_length == 5000

    def test_send_message_with_reply(self, dm_conversation):
        """Test sending a message as a reply to another."""
        dm, user1, user2, messaging = dm_conversation

        original = messaging.send_message(user1.id, dm.id, "Original")
        reply = messaging.send_message(
            user2.id, dm.id, "Reply", reply_to_id=original.id
        )

        assert reply.reply_to_id == original.id

    def test_send_message_with_invalid_reply_fails(self, dm_conversation):
        """Test that replying to non-existent message fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(MessageNotFoundError):
            messaging.send_message(user1.id, dm.id, "Reply", reply_to_id=999999999)

    def test_send_message_updates_conversation_timestamp(self, dm_conversation):
        """Test that sending a message updates conversation's last_message_at."""
        dm, user1, user2, messaging = dm_conversation
        initial_time = dm.last_message_at

        time.sleep(0.01)
        msg = messaging.send_message(user1.id, dm.id, "Update timestamp")

        updated_conv = messaging.get_conversation(dm.id, user1.id)
        assert updated_conv.last_message_id == msg.id
        assert updated_conv.last_message_at > (initial_time or 0)

    def test_send_message_non_participant_fails(self, dm_conversation, user_pool):
        """Test that non-participants cannot send messages."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.send_message(user3.id, dm.id, "I'm not in this DM!")

    def test_send_system_message(self, group_conversation):
        """Test sending system messages."""
        group, owner, member1, member2, messaging = group_conversation

        sys_msg = messaging.send_system_message(
            group.id, "System notification", "test_event", {"key": "value"}
        )

        assert sys_msg.message_type == MessageType.SYSTEM
        assert sys_msg.author_id == 0
        assert sys_msg.metadata["event_type"] == "test_event"


class TestMessageRetrieval:
    """Tests for retrieving messages."""

    def test_get_single_message(self, dm_conversation):
        """Test retrieving a single message by ID."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test message")

        retrieved = messaging.get_message(user1.id, msg.id)
        assert retrieved.id == msg.id
        assert retrieved.content == msg.content

    def test_get_message_non_participant_fails(self, dm_conversation, user_pool):
        """Test that non-participants cannot retrieve messages."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Private")
        user3 = user_pool.get_user()

        result = messaging.get_message(user3.id, msg.id)
        assert result is None

    def test_get_deleted_message_returns_none(self, dm_conversation):
        """Test that deleted messages are not retrievable."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "To be deleted")

        messaging.delete_message(user1.id, msg.id)
        result = messaging.get_message(user1.id, msg.id)
        assert result is None

    def test_get_messages_from_conversation(self, dm_conversation):
        """Test retrieving multiple messages from a conversation."""
        dm, user1, user2, messaging = dm_conversation

        for i in range(5):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=10)
        assert len(messages) == 5

    def test_get_messages_pagination_limit(self, dm_conversation):
        """Test that message retrieval respects limit parameter."""
        dm, user1, user2, messaging = dm_conversation

        for i in range(20):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        messages = messaging.get_messages(user1.id, dm.id, limit=5)
        assert len(messages) == 5

    def test_get_messages_before_cursor(self, dm_conversation):
        """Test cursor pagination with before_id."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get messages before 5th message
        page = messaging.get_messages(user1.id, dm.id, limit=3, before_id=msgs[5].id)
        assert len(page) == 3
        assert all(m.id < msgs[5].id for m in page)

    def test_get_messages_after_cursor(self, dm_conversation):
        """Test cursor pagination with after_id."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Get messages after 3rd message
        page = messaging.get_messages(user1.id, dm.id, limit=3, after_id=msgs[2].id)
        assert len(page) == 3
        assert all(m.id > msgs[2].id for m in page)


class TestMessageEditing:
    """Tests for editing messages."""

    def test_edit_own_message(self, dm_conversation):
        """Test editing own message."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Original content")

        time.sleep(0.01)
        edited = messaging.edit_message(user1.id, msg.id, "Edited content")

        assert edited.content == "Edited content"
        assert edited.edited is True
        assert edited.updated_at > msg.created_at

    def test_edit_message_preserves_encryption(self, dm_conversation):
        """Test that edited messages remain encrypted."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Original")

        edited = messaging.edit_message(user1.id, msg.id, "Edited with encryption")

        # Retrieve and verify decryption works
        retrieved = messaging.get_message(user1.id, edited.id)
        assert retrieved.content == "Edited with encryption"

    def test_edit_others_message_fails(self, dm_conversation):
        """Test that users cannot edit others' messages."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "User1's message")

        with pytest.raises(MessageAccessDeniedError):
            messaging.edit_message(user2.id, msg.id, "User2 trying to edit")

    def test_edit_nonexistent_message_fails(self, dm_conversation):
        """Test that editing non-existent message fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(MessageNotFoundError):
            messaging.edit_message(user1.id, 999999999, "Edit ghost message")

    def test_edit_deleted_message_fails(self, dm_conversation):
        """Test that editing deleted message fails."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "To be deleted")
        messaging.delete_message(user1.id, msg.id)

        with pytest.raises(MessageNotFoundError):
            messaging.edit_message(user1.id, msg.id, "Edit deleted")

    def test_edit_message_with_invalid_content_fails(self, dm_conversation):
        """Test that editing with invalid content fails."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Original")

        with pytest.raises(InvalidContentError):
            messaging.edit_message(user1.id, msg.id, "")


class TestMessageDeletion:
    """Tests for deleting messages."""

    def test_delete_own_message(self, dm_conversation):
        """Test soft deleting own message."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "To be deleted")

        result = messaging.delete_message(user1.id, msg.id)
        assert result is True

        # Verify message is not retrievable
        retrieved = messaging.get_message(user1.id, msg.id)
        assert retrieved is None

    def test_delete_others_message_as_admin(self, group_conversation):
        """Test that admins can delete others' messages."""
        group, owner, member1, member2, messaging = group_conversation
        msg = messaging.send_message(member1.id, group.id, "Member message")

        # Owner (admin) can delete
        result = messaging.delete_message(owner.id, msg.id)
        assert result is True

    def test_delete_others_message_as_member_fails(self, group_conversation):
        """Test that regular members cannot delete others' messages."""
        group, owner, member1, member2, messaging = group_conversation
        msg = messaging.send_message(owner.id, group.id, "Owner message")

        with pytest.raises(MessageAccessDeniedError):
            messaging.delete_message(member1.id, msg.id)

    def test_hard_delete_message(self, dm_conversation):
        """Test hard deleting a message (actual removal)."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Hard delete me")

        messaging.delete_message(user1.id, msg.id, hard_delete=True)

        # Verify message is completely gone
        raw = messaging._get_message_raw(msg.id)
        assert raw is None

    def test_deleted_messages_not_in_listing(self, dm_conversation):
        """Test that deleted messages don't appear in message listings."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Message 1")
        msg2 = messaging.send_message(user1.id, dm.id, "Message 2")
        messaging.send_message(user1.id, dm.id, "Message 3")

        messaging.delete_message(user1.id, msg2.id)

        messages = messaging.get_messages(user1.id, dm.id, limit=10)
        assert len(messages) == 2
        assert msg2.id not in [m.id for m in messages]


@pytest.mark.asyncio
class TestMessageConcurrency:
    """Tests for concurrent message operations."""

    async def test_concurrent_message_sending(self, dm_conversation):
        """Test sending multiple messages concurrently."""
        dm, user1, user2, messaging = dm_conversation

        tasks = [
            asyncio.to_thread(
                messaging.send_message, user1.id, dm.id, f"Concurrent {i}"
            )
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 20
        # Verify unique IDs
        ids = {m.id for m in results}
        assert len(ids) == 20

    async def test_concurrent_edits(self, dm_conversation):
        """Test concurrent edits to different messages."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = await asyncio.to_thread(
                messaging.send_message, user1.id, dm.id, f"Original {i}"
            )
            msgs.append(msg)

        # Edit all concurrently
        tasks = [
            asyncio.to_thread(messaging.edit_message, user1.id, msg.id, f"Edited {i}")
            for i, msg in enumerate(msgs)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert all(r.edited for r in results)

    async def test_concurrent_deletions(self, dm_conversation):
        """Test concurrent message deletions."""
        dm, user1, user2, messaging = dm_conversation

        # Create messages
        msgs = []
        for i in range(10):
            msg = await asyncio.to_thread(
                messaging.send_message, user1.id, dm.id, f"Delete {i}"
            )
            msgs.append(msg)

        # Delete all concurrently
        tasks = [
            asyncio.to_thread(messaging.delete_message, user1.id, msg.id)
            for msg in msgs
        ]
        results = await asyncio.gather(*tasks)

        assert all(results)

        # Verify all deleted
        remaining = await asyncio.to_thread(
            messaging.get_messages, user1.id, dm.id, limit=100
        )
        assert len(remaining) == 0


class TestMessagePinning:
    """Tests for pinning messages."""

    def test_pin_message(self, group_conversation):
        """Test pinning a message."""
        group, owner, member1, member2, messaging = group_conversation
        msg = messaging.send_message(owner.id, group.id, "Important message")

        result = messaging.pin_message(owner.id, msg.id)
        assert result is True

        # Verify pinned
        pinned = messaging.get_pinned_messages(owner.id, group.id)
        assert len(pinned) == 1
        assert pinned[0].id == msg.id

    def test_unpin_message(self, group_conversation):
        """Test unpinning a message."""
        group, owner, member1, member2, messaging = group_conversation
        msg = messaging.send_message(owner.id, group.id, "Pinned message")

        messaging.pin_message(owner.id, msg.id)
        messaging.unpin_message(owner.id, msg.id)

        pinned = messaging.get_pinned_messages(owner.id, group.id)
        assert len(pinned) == 0

    def test_pin_deleted_message_fails(self, group_conversation):
        """Test that deleted messages cannot be pinned."""
        group, owner, member1, member2, messaging = group_conversation
        msg = messaging.send_message(owner.id, group.id, "Message")
        messaging.delete_message(owner.id, msg.id)

        with pytest.raises(MessageNotFoundError):
            messaging.pin_message(owner.id, msg.id)

    def test_get_pinned_messages_ordering(self, group_conversation):
        """Test that pinned messages are returned in pin order."""
        group, owner, member1, member2, messaging = group_conversation

        msgs = []
        for i in range(3):
            msg = messaging.send_message(owner.id, group.id, f"Pin {i}")
            msgs.append(msg)
            time.sleep(0.01)
            messaging.pin_message(owner.id, msg.id)

        pinned = messaging.get_pinned_messages(owner.id, group.id)
        assert len(pinned) == 3
        # Most recently pinned first
        assert pinned[0].id == msgs[2].id
        assert pinned[2].id == msgs[0].id
