"""
Tests for thread lock functionality.
"""

import pytest
from src.core.threads import (
    ThreadState,
    ThreadNotFoundError,
    ThreadLockedError,
    PermissionDeniedError,
)


class TestLockThread:
    """Tests for locking threads."""

    def test_lock_thread(self, server_with_channel):
        """Test locking a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Lock Test"
        )

        locked = threads.lock_thread(owner.id, thread.id)

        assert locked.locked is True

    def test_lock_thread_by_owner(self, server_with_channel):
        """Test that owner can lock their thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id,
            channel_id=channel.id,
            name="Owner Lock Test"
        )

        locked = threads.lock_thread(member1.id, thread.id)
        assert locked.locked is True

    def test_lock_nonexistent_thread_fails(self, server_with_channel):
        """Test locking nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.lock_thread(owner.id, 999999999)

    def test_lock_without_permission_fails(self, server_with_channel):
        """Test that non-owner without permission cannot lock."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Permission Lock Test"
        )

        threads.join_thread(member1.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.lock_thread(member1.id, thread.id)

    def test_locked_thread_prevents_messages(self, server_with_channel):
        """Test that locked thread prevents new messages."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Locked Messages Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.lock_thread(owner.id, thread.id)

        with pytest.raises(ThreadLockedError):
            threads.send_message(member1.id, thread.id, "Should fail")

    def test_owner_can_send_to_locked_thread(self, server_with_channel):
        """Test that owner can still send to locked thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Owner Locked Send Test"
        )

        threads.lock_thread(owner.id, thread.id)

        msg = threads.send_message(owner.id, thread.id, "Owner message")
        assert msg is not None


class TestUnlockThread:
    """Tests for unlocking threads."""

    def test_unlock_thread(self, server_with_channel):
        """Test unlocking a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Unlock Test"
        )

        threads.lock_thread(owner.id, thread.id)
        unlocked = threads.unlock_thread(owner.id, thread.id)

        assert unlocked.locked is False

    def test_unlock_unlocked_thread(self, server_with_channel):
        """Test unlocking already unlocked thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Already Unlocked Test"
        )

        unlocked = threads.unlock_thread(owner.id, thread.id)
        assert unlocked.locked is False

    def test_unlock_nonexistent_thread_fails(self, server_with_channel):
        """Test unlocking nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.unlock_thread(owner.id, 999999999)

    def test_unlock_without_permission_fails(self, server_with_channel):
        """Test that non-owner without permission cannot unlock."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Permission Unlock Test"
        )

        threads.lock_thread(owner.id, thread.id)
        threads.join_thread(member1.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.unlock_thread(member1.id, thread.id)

    def test_unlocked_thread_allows_messages(self, server_with_channel):
        """Test that unlocked thread allows messages again."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Unlocked Messages Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.lock_thread(owner.id, thread.id)
        threads.unlock_thread(owner.id, thread.id)

        msg = threads.send_message(member1.id, thread.id, "Should work")
        assert msg is not None


class TestLockAndArchive:
    """Tests for lock and archive interaction."""

    def test_lock_archived_thread(self, server_with_channel):
        """Test locking an archived thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Lock Archived Test"
        )

        threads.archive_thread(owner.id, thread.id)
        locked = threads.lock_thread(owner.id, thread.id)

        assert locked.locked is True
        assert locked.state == ThreadState.ARCHIVED

    def test_archive_locked_thread(self, server_with_channel):
        """Test archiving a locked thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archive Locked Test"
        )

        threads.lock_thread(owner.id, thread.id)
        archived = threads.archive_thread(owner.id, thread.id)

        assert archived.locked is True
        assert archived.state == ThreadState.ARCHIVED
