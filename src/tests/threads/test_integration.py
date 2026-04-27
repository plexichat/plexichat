"""
Tests for thread integration with other modules.
"""

from src.core.threads import (
    ThreadType,
    ThreadState,
    AutoArchiveDuration,
)


class TestServerIntegration:
    """Tests for integration with servers module."""

    def test_thread_inherits_server_id(self, server_with_channel):
        """Test that thread inherits server ID from channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Server ID Test"
        )

        assert thread.server_id == server.id

    def test_thread_in_different_channels(self, server_with_channel):
        """Test creating threads in different channels."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        channel2 = servers.create_channel(
            owner.id, server.id, "channel2", channel_type=servers.ChannelType.TEXT
        )

        thread1 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread in Channel 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id, channel_id=channel2.id, name="Thread in Channel 2"
        )

        assert thread1.channel_id == channel.id
        assert thread2.channel_id == channel2.id
        assert thread1.server_id == thread2.server_id

    def test_server_member_can_create_thread(self, server_with_channel):
        """Test that server member can create thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id, channel_id=channel.id, name="Member Thread"
        )

        assert thread.owner_id == member1.id


class TestMultipleThreads:
    """Tests for multiple thread scenarios."""

    def test_multiple_threads_same_channel(self, server_with_channel):
        """Test multiple threads in same channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=member1.id, channel_id=channel.id, name="Thread 2"
        )

        thread3 = threads.create_thread(
            user_id=member2.id, channel_id=channel.id, name="Thread 3"
        )

        active = threads.get_active_threads(owner.id, channel.id)
        thread_ids = [t.id for t in active]

        assert thread1.id in thread_ids
        assert thread2.id in thread_ids
        assert thread3.id in thread_ids

    def test_user_in_multiple_threads(self, server_with_channel):
        """Test user joining multiple threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread 2"
        )

        threads.join_thread(member1.id, thread1.id)
        threads.join_thread(member1.id, thread2.id)

        user_threads = threads.get_user_threads(member1.id)
        thread_ids = [t.id for t in user_threads]

        assert thread1.id in thread_ids
        assert thread2.id in thread_ids

    def test_mixed_thread_types(self, server_with_channel):
        """Test mixed public and private threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        public = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Public Thread"
        )

        private = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Thread",
            thread_type=ThreadType.PRIVATE,
        )

        active = threads.get_active_threads(member1.id, channel.id)
        thread_ids = [t.id for t in active]

        assert public.id in thread_ids
        assert private.id not in thread_ids


class TestThreadLifecycle:
    """Tests for complete thread lifecycle."""

    def test_full_thread_lifecycle(self, server_with_channel):
        """Test complete thread lifecycle from creation to deletion."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Lifecycle Thread"
        )
        assert thread.state == ThreadState.ACTIVE

        threads.join_thread(member1.id, thread.id)
        threads.join_thread(member2.id, thread.id)

        members = threads.get_thread_members(owner.id, thread.id)
        assert len(members) == 3

        threads.send_message(owner.id, thread.id, "Message 1")
        threads.send_message(member1.id, thread.id, "Message 2")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.message_count == 2

        archived = threads.archive_thread(owner.id, thread.id)
        assert archived.state == ThreadState.ARCHIVED

        threads.send_message(member2.id, thread.id, "Unarchive message")
        unarchived = threads.get_thread(owner.id, thread.id)
        assert unarchived.state == ThreadState.ACTIVE

        locked = threads.lock_thread(owner.id, thread.id)
        assert locked.locked is True

        result = threads.delete_thread(owner.id, thread.id)
        assert result is True

        deleted = threads.get_thread(owner.id, thread.id)
        assert deleted is None

    def test_thread_with_all_durations(self, server_with_channel):
        """Test creating threads with all auto-archive durations."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        durations = [
            AutoArchiveDuration.ONE_HOUR,
            AutoArchiveDuration.ONE_DAY,
            AutoArchiveDuration.THREE_DAYS,
            AutoArchiveDuration.SEVEN_DAYS,
        ]

        for duration in durations:
            thread = threads.create_thread(
                user_id=owner.id,
                channel_id=channel.id,
                name=f"Thread {duration.value}",
                auto_archive_duration=duration,
            )
            assert thread.auto_archive_duration == duration


class TestConcurrentOperations:
    """Tests for concurrent thread operations."""

    def test_multiple_users_join_simultaneously(self, server_with_channel):
        """Test multiple users joining thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Concurrent Join Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.join_thread(member2.id, thread.id)

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.member_count == 3

    def test_multiple_messages_update_count(self, server_with_channel):
        """Test multiple messages updating count correctly."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Message Count Test"
        )

        for i in range(10):
            threads.send_message(owner.id, thread.id, f"Message {i}")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.message_count == 10
