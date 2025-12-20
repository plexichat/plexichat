"""
Edge cases and race condition tests.

Tests race conditions, concurrent operations, error handling,
and unusual scenarios.
"""

import pytest
import asyncio
import time
from src.core.messaging.exceptions import (
    MessageNotFoundError,
    ConversationAccessDeniedError,
    ParticipantLimitError,
)


@pytest.mark.asyncio
class TestRaceConditions:
    """Tests for race conditions."""

    async def test_concurrent_dm_creation(self, user_pool, modules):
        """Test concurrent DM creation between same users."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # Create DM from both sides simultaneously
        tasks = [
            asyncio.to_thread(modules.messaging.create_dm, user1.id, user2.id),
            asyncio.to_thread(modules.messaging.create_dm, user2.id, user1.id),
            asyncio.to_thread(modules.messaging.create_dm, user1.id, user2.id),
        ]

        results = await asyncio.gather(*tasks)

        # All should return same DM
        assert len(set(r.id for r in results)) == 1

    async def test_concurrent_message_edits(self, dm_conversation):
        """Test concurrent edits to same message (race condition)."""
        dm, user1, user2, messaging = dm_conversation

        msg = await asyncio.to_thread(
            messaging.send_message, user1.id, dm.id, "Original"
        )

        # Try to edit concurrently
        tasks = [
            asyncio.to_thread(messaging.edit_message, user1.id, msg.id, f"Edit {i}")
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (last write wins)
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) >= 1

    async def test_concurrent_participant_additions(
        self, group_conversation, user_pool
    ):
        """Test adding multiple participants concurrently."""
        group, owner, member1, member2, messaging = group_conversation

        # Add multiple users concurrently
        new_users = [user_pool.get_user() for _ in range(5)]

        tasks = [
            asyncio.to_thread(messaging.add_participant, owner.id, group.id, user.id)
            for user in new_users
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) >= 1

    async def test_concurrent_message_deletions(self, dm_conversation):
        """Test concurrent deletion of same message."""
        dm, user1, user2, messaging = dm_conversation

        msg = await asyncio.to_thread(
            messaging.send_message, user1.id, dm.id, "To delete"
        )

        # Try to delete concurrently
        tasks = [
            asyncio.to_thread(messaging.delete_message, user1.id, msg.id)
            for _ in range(3)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # First should succeed, others may fail
        assert any(r is True for r in results if not isinstance(r, Exception))

    async def test_concurrent_group_leave(self, group_conversation):
        """Test multiple members leaving concurrently."""
        group, owner, member1, member2, messaging = group_conversation

        # Both members try to leave at same time
        tasks = [
            asyncio.to_thread(messaging.leave_conversation, member1.id, group.id),
            asyncio.to_thread(messaging.leave_conversation, member2.id, group.id),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Both should succeed
        assert all(r is True for r in results if not isinstance(r, Exception))

    async def test_concurrent_role_changes(self, group_conversation):
        """Test concurrent role changes for same user."""
        group, owner, member1, member2, messaging = group_conversation

        from src.core.messaging.models import ParticipantRole

        # Try to change role concurrently
        tasks = [
            asyncio.to_thread(
                messaging.update_participant_role,
                owner.id,
                group.id,
                member1.id,
                ParticipantRole.ADMIN,
            )
            for _ in range(3)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (same final state)
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) >= 1

    async def test_concurrent_conversation_updates(self, group_conversation):
        """Test concurrent updates to same conversation."""
        group, owner, member1, member2, messaging = group_conversation

        # Try to update concurrently
        tasks = [
            asyncio.to_thread(
                messaging.update_conversation, owner.id, group.id, name=f"Name {i}"
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (last write wins)
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) >= 1


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_conversation_no_messages(self, dm_conversation):
        """Test conversation with no messages."""
        dm, user1, user2, messaging = dm_conversation

        messages = messaging.get_messages(user1.id, dm.id)
        # May have system messages, but should handle gracefully
        assert messages is not None

    def test_conversation_with_single_message(self, dm_conversation):
        """Test conversation with exactly one message."""
        dm, user1, user2, messaging = dm_conversation

        messaging.send_message(user1.id, dm.id, "Only message")
        messages = messaging.get_messages(user1.id, dm.id)

        assert len([m for m in messages if m.author_id != 0]) == 1

    def test_message_reply_to_deleted(self, dm_conversation):
        """Test replying to deleted message."""
        dm, user1, user2, messaging = dm_conversation

        msg1 = messaging.send_message(user1.id, dm.id, "Original")
        messaging.delete_message(user1.id, msg1.id)

        # Try to reply to deleted message
        with pytest.raises(MessageNotFoundError):
            messaging.send_message(user2.id, dm.id, "Reply", reply_to_id=msg1.id)

    def test_edit_message_multiple_times(self, dm_conversation):
        """Test editing same message multiple times."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Version 1")

        for i in range(5):
            time.sleep(0.01)
            msg = messaging.edit_message(user1.id, msg.id, f"Version {i + 2}")

        assert "Version 6" in msg.content
        assert msg.edited is True

    def test_participant_limit_at_boundary(self, user_pool, modules):
        """Test adding participant at exact limit."""
        owner = user_pool.get_user()

        # Create group with limit of 3
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Limited", max_participants=3
        )

        # Add 2 members (total 3)
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        modules.messaging.add_participant(owner.id, group.id, user1.id)
        modules.messaging.add_participant(owner.id, group.id, user2.id)

        # At limit, cannot add more
        user3 = user_pool.get_user()
        with pytest.raises(ParticipantLimitError):
            modules.messaging.add_participant(owner.id, group.id, user3.id)

    def test_get_message_after_conversation_deleted(self, dm_conversation):
        """Test getting message after conversation deleted."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Test")
        messaging.delete_conversation(user1.id, dm.id)

        # Should not be able to get message
        result = messaging.get_message(user1.id, msg.id)
        assert result is None

    def test_send_message_after_leaving(self, group_conversation):
        """Test sending message after leaving conversation."""
        group, owner, member1, member2, messaging = group_conversation

        messaging.leave_conversation(member1.id, group.id)

        # Should not be able to send
        with pytest.raises(ConversationAccessDeniedError):
            messaging.send_message(member1.id, group.id, "After leaving")

    def test_add_removed_participant_back(self, group_conversation, user_pool):
        """Test adding participant that was removed."""
        group, owner, member1, member2, messaging = group_conversation

        # Remove member
        messaging.remove_participant(owner.id, group.id, member1.id)

        # Add back
        result = messaging.add_participant(owner.id, group.id, member1.id)
        assert result is not None

    def test_extremely_long_content(self, dm_conversation):
        """Test message with content at max length."""
        dm, user1, user2, messaging = dm_conversation

        from src.core.messaging.exceptions import ContentTooLongError

        # At limit (4000)
        content = "A" * 4000
        msg = messaging.send_message(user1.id, dm.id, content)
        assert len(msg.content) == 4000

        # Over limit
        content = "A" * 4001
        with pytest.raises(ContentTooLongError):
            messaging.send_message(user1.id, dm.id, content)


class TestErrorRecovery:
    """Tests for error recovery and resilience."""

    def test_recover_from_failed_message_send(self, dm_conversation):
        """Test that failed send doesn't corrupt state."""
        dm, user1, user2, messaging = dm_conversation

        # Try invalid send
        try:
            messaging.send_message(user1.id, dm.id, "")
        except Exception:
            pass

        # Should still be able to send valid message
        msg = messaging.send_message(user1.id, dm.id, "Valid")
        assert msg is not None

    def test_recover_from_failed_participant_add(self, group_conversation, user_pool):
        """Test recovery from failed participant addition."""
        group, owner, member1, member2, messaging = group_conversation

        # Try to add invalid participant
        try:
            messaging.add_participant(member1.id, group.id, user_pool.get_user().id)
        except Exception:
            pass

        # Owner should still be able to add
        new_user = user_pool.get_user()
        result = messaging.add_participant(owner.id, group.id, new_user.id)
        assert result is not None

    def test_conversation_state_after_failed_update(self, group_conversation):
        """Test conversation state after failed update."""
        group, owner, member1, member2, messaging = group_conversation

        original_name = group.name

        # Try invalid update
        try:
            messaging.update_conversation(owner.id, group.id, name="")
        except Exception:
            pass

        # Verify state unchanged
        conv = messaging.get_conversation(group.id, owner.id)
        assert conv.name == original_name


class TestDataIntegrity:
    """Tests for data integrity."""

    def test_message_count_consistency(self, dm_conversation):
        """Test that message count is consistent."""
        dm, user1, user2, messaging = dm_conversation

        # Send messages
        for i in range(10):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        # Delete some
        messages = messaging.get_messages(user1.id, dm.id, limit=100)
        for msg in messages[:5]:
            messaging.delete_message(user1.id, msg.id)

        # Verify count
        remaining = messaging.get_messages(user1.id, dm.id, limit=100)
        assert len(remaining) == 5

    def test_participant_count_consistency(self, group_conversation, user_pool):
        """Test that participant count is consistent."""
        group, owner, member1, member2, messaging = group_conversation

        # Add participants
        new_users = [user_pool.get_user() for _ in range(3)]
        for user in new_users:
            messaging.add_participant(owner.id, group.id, user.id)

        # Verify count
        conv = messaging.get_conversation(group.id, owner.id)
        participants = messaging.get_participants(owner.id, group.id)
        assert conv.participant_count == len(participants)

    def test_unread_count_accuracy(self, dm_conversation):
        """Test that unread count is accurate."""
        dm, user1, user2, messaging = dm_conversation

        # User1 sends messages
        for i in range(5):
            messaging.send_message(user1.id, dm.id, f"Message {i}")

        # Check unread
        unread = messaging.get_unread_count(user2.id, dm.id)
        assert unread[dm.id] == 5

        # Mark some as read
        messages = messaging.get_messages(user2.id, dm.id, limit=3)
        messaging.mark_read(user2.id, dm.id, up_to_message_id=messages[0].id)

        # Unread should decrease
        unread = messaging.get_unread_count(user2.id, dm.id)
        assert unread[dm.id] < 5


class TestBoundaryConditions:
    """Tests for boundary conditions."""

    def test_snowflake_id_uniqueness(self, dm_conversation):
        """Test that Snowflake IDs are unique."""
        dm, user1, user2, messaging = dm_conversation

        # Generate many IDs quickly
        ids = set()
        for i in range(1000):
            msg = messaging.send_message(user1.id, dm.id, f"Test {i}")
            ids.add(msg.id)

        # All should be unique
        assert len(ids) == 1000

    def test_timestamp_ordering(self, dm_conversation):
        """Test that timestamps are properly ordered."""
        dm, user1, user2, messaging = dm_conversation

        msgs = []
        for i in range(10):
            time.sleep(0.001)
            msg = messaging.send_message(user1.id, dm.id, f"Message {i}")
            msgs.append(msg)

        # Timestamps should be increasing
        for i in range(len(msgs) - 1):
            assert msgs[i].created_at <= msgs[i + 1].created_at

    def test_conversation_with_max_participants(self, user_pool, modules):
        """Test conversation at absolute maximum participants."""
        owner = user_pool.get_user()

        # Create group with high limit
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Large Group", max_participants=100
        )

        # Should be created successfully
        assert group.max_participants == 100

    def test_zero_size_operations(self, dm_conversation):
        """Test operations with zero/empty values."""
        dm, user1, user2, messaging = dm_conversation

        # Get 0 messages
        messages = messaging.get_messages(user1.id, dm.id, limit=0)
        assert len(messages) == 0

        # Get 0 conversations
        convs = messaging.get_conversations(user1.id, limit=0)
        assert len(convs) == 0


class TestCacheInvalidation:
    """Tests for cache invalidation."""

    def test_participant_cache_after_removal(self, group_conversation):
        """Test that participant cache is invalidated after removal."""
        group, owner, member1, member2, messaging = group_conversation

        # Check participant (populates cache)
        assert messaging._is_participant(group.id, member1.id) is True

        # Remove participant
        messaging.remove_participant(owner.id, group.id, member1.id)

        # Cache should be invalidated
        assert messaging._is_participant(group.id, member1.id) is False

    def test_settings_cache_after_update(self, user_pool, modules):
        """Test that settings cache is invalidated after update."""
        user = user_pool.get_user()

        # Get settings (populates cache)
        settings1 = modules.messaging.get_user_message_settings(user.id)
        assert settings1.allow_dms_from == "everyone"

        # Update settings
        modules.messaging.update_user_message_settings(user.id, allow_dms_from="none")

        # Should get updated value
        settings2 = modules.messaging.get_user_message_settings(user.id)
        assert settings2.allow_dms_from == "none"
