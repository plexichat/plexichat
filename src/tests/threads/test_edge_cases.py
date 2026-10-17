"""
Tests for thread edge cases and error handling.
"""

import pytest
from src.core.threads import (
    ThreadState,
    ThreadNotFoundError,
    ThreadNameError,
    ThreadMemberNotFoundError,
    ThreadMemberExistsError,
    ChannelNotFoundError,
)


class TestDeletedThreads:
    """Tests for deleted thread handling."""

    def test_get_deleted_thread_returns_none(self, server_with_channel):
        """Test that getting deleted thread returns None."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Delete Test"
        )

        threads.delete_thread(owner.id, thread.id)

        result = threads.get_thread(owner.id, thread.id)
        assert result is None

    def test_deleted_thread_not_in_active_list(self, server_with_channel):
        """Test that deleted thread not in active threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Delete List Test"
        )

        threads.delete_thread(owner.id, thread.id)

        active = threads.get_active_threads(owner.id, channel.id)
        thread_ids = [t.id for t in active]
        assert thread.id not in thread_ids

    def test_deleted_thread_not_in_user_threads(self, server_with_channel):
        """Test that deleted thread not in user threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Delete User Test"
        )

        threads.delete_thread(owner.id, thread.id)

        user_threads = threads.get_user_threads(owner.id)
        thread_ids = [t.id for t in user_threads]
        assert thread.id not in thread_ids


class TestInvalidOperations:
    """Tests for invalid operations."""

    def test_join_nonexistent_thread(self, server_with_channel):
        """Test joining nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.join_thread(member1.id, 999999999)

    def test_leave_nonexistent_thread(self, server_with_channel):
        """Test leaving nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.leave_thread(member1.id, 999999999)

    def test_send_to_nonexistent_thread(self, server_with_channel):
        """Test sending to nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.send_message(owner.id, 999999999, "Test")

    def test_archive_nonexistent_thread(self, server_with_channel):
        """Test archiving nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.archive_thread(owner.id, 999999999)

    def test_lock_nonexistent_thread(self, server_with_channel):
        """Test locking nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.lock_thread(owner.id, 999999999)

    def test_update_nonexistent_thread(self, server_with_channel):
        """Test updating nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.update_thread(owner.id, 999999999, name="New Name")

    def test_delete_nonexistent_thread(self, server_with_channel):
        """Test deleting nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.delete_thread(owner.id, 999999999)


class TestNameValidation:
    """Tests for thread name validation."""

    def test_empty_name(self, server_with_channel):
        """Test empty thread name."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(user_id=owner.id, channel_id=channel.id, name="")

    def test_whitespace_only_name(self, server_with_channel):
        """Test whitespace-only thread name."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(
                user_id=owner.id, channel_id=channel.id, name="   \t\n  "
            )

    def test_name_too_long(self, server_with_channel):
        """Test thread name exceeding max length."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(
                user_id=owner.id, channel_id=channel.id, name="x" * 101
            )

    def test_name_exactly_max_length(self, server_with_channel):
        """Test thread name at exactly max length."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="x" * 100
        )

        assert len(thread.name) == 100

    def test_name_with_special_characters(self, server_with_channel):
        """Test thread name with special characters."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Test-Thread_123!@#"
        )

        assert thread.name == "Test-Thread_123!@#"

    def test_update_with_empty_name(self, server_with_channel):
        """Test updating thread with empty name."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Original Name"
        )

        with pytest.raises(ThreadNameError):
            threads.update_thread(owner.id, thread.id, name="")


class TestMembershipEdgeCases:
    """Tests for membership edge cases."""

    def test_double_join(self, server_with_channel):
        """Test joining thread twice."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Double Join Test"
        )

        threads.join_thread(member1.id, thread.id)

        with pytest.raises(ThreadMemberExistsError):
            threads.join_thread(member1.id, thread.id)

    def test_leave_without_joining(self, server_with_channel):
        """Test leaving thread without being member."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Leave Without Join Test"
        )

        with pytest.raises(ThreadMemberNotFoundError):
            threads.leave_thread(member1.id, thread.id)

    def test_add_existing_member(self, server_with_channel):
        """Test adding already existing member."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Add Existing Test"
        )

        threads.add_member(owner.id, thread.id, member1.id)

        with pytest.raises(ThreadMemberExistsError):
            threads.add_member(owner.id, thread.id, member1.id)

    def test_remove_non_member(self, server_with_channel):
        """Test removing non-member."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Remove Non Member Test"
        )

        with pytest.raises(ThreadMemberNotFoundError):
            threads.remove_member(owner.id, thread.id, member1.id)


class TestChannelEdgeCases:
    """Tests for channel-related edge cases."""

    def test_create_thread_invalid_channel(self, server_with_channel):
        """Test creating thread in invalid channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.create_thread(
                user_id=owner.id, channel_id=999999999, name="Invalid Channel Test"
            )

    def test_get_active_threads_invalid_channel(self, server_with_channel):
        """Test getting active threads from invalid channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.get_active_threads(owner.id, 999999999)

    def test_get_archived_threads_invalid_channel(self, server_with_channel):
        """Test getting archived threads from invalid channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.get_archived_threads(owner.id, 999999999)


class TestStateTransitions:
    """Tests for thread state transitions."""

    def test_archive_active_thread(self, server_with_channel):
        """Test archiving active thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Archive Active Test"
        )

        assert thread.state == ThreadState.ACTIVE

        archived = threads.archive_thread(owner.id, thread.id)
        assert archived.state == ThreadState.ARCHIVED

    def test_unarchive_archived_thread(self, server_with_channel):
        """Test unarchiving archived thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Unarchive Test"
        )

        threads.archive_thread(owner.id, thread.id)
        unarchived = threads.unarchive_thread(owner.id, thread.id)

        assert unarchived.state == ThreadState.ACTIVE

    def test_lock_unlock_cycle(self, server_with_channel):
        """Test lock/unlock cycle."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Lock Cycle Test"
        )

        assert thread.locked is False

        locked = threads.lock_thread(owner.id, thread.id)
        assert locked.locked is True

        unlocked = threads.unlock_thread(owner.id, thread.id)
        assert unlocked.locked is False
