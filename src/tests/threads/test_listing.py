"""
Tests for thread listing functionality.
"""

import pytest
from src.core.threads import (
    ThreadType,
    ChannelNotFoundError,
)


class TestGetActiveThreads:
    """Tests for getting active threads."""

    def test_get_active_threads(self, server_with_channel):
        """Test getting active threads in a channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active Thread 2"
        )

        active = threads.get_active_threads(owner.id, channel.id)

        assert len(active) >= 2
        thread_ids = [t.id for t in active]
        assert thread1.id in thread_ids
        assert thread2.id in thread_ids

    def test_get_active_threads_excludes_archived(self, server_with_channel):
        """Test that archived threads are excluded."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active Thread"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived Thread"
        )

        threads.archive_thread(owner.id, thread2.id)

        active = threads.get_active_threads(owner.id, channel.id)

        thread_ids = [t.id for t in active]
        assert thread1.id in thread_ids
        assert thread2.id not in thread_ids

    def test_get_active_threads_invalid_channel(self, server_with_channel):
        """Test getting active threads from invalid channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.get_active_threads(owner.id, 999999999)

    def test_get_active_threads_empty_channel(self, server_with_channel):
        """Test getting active threads from channel with no threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        new_channel = servers.create_channel(
            owner.id, server.id, "empty-channel",
            channel_type=servers.ChannelType.TEXT
        )

        active = threads.get_active_threads(owner.id, new_channel.id)
        assert len(active) == 0


class TestGetArchivedThreads:
    """Tests for getting archived threads."""

    def test_get_archived_threads(self, server_with_channel):
        """Test getting archived threads in a channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived Thread 2"
        )

        threads.archive_thread(owner.id, thread1.id)
        threads.archive_thread(owner.id, thread2.id)

        archived = threads.get_archived_threads(owner.id, channel.id)

        assert len(archived) >= 2
        thread_ids = [t.id for t in archived]
        assert thread1.id in thread_ids
        assert thread2.id in thread_ids

    def test_get_archived_threads_excludes_active(self, server_with_channel):
        """Test that active threads are excluded."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active Thread"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived Thread"
        )

        threads.archive_thread(owner.id, thread2.id)

        archived = threads.get_archived_threads(owner.id, channel.id)

        thread_ids = [t.id for t in archived]
        assert thread1.id not in thread_ids
        assert thread2.id in thread_ids

    def test_get_archived_threads_with_limit(self, server_with_channel):
        """Test getting archived threads with limit."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        for i in range(5):
            thread = threads.create_thread(
                user_id=owner.id,
                channel_id=channel.id,
                name=f"Archived Thread {i}"
            )
            threads.archive_thread(owner.id, thread.id)

        archived = threads.get_archived_threads(owner.id, channel.id, limit=3)
        assert len(archived) <= 3

    def test_get_archived_threads_invalid_channel(self, server_with_channel):
        """Test getting archived threads from invalid channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.get_archived_threads(owner.id, 999999999)


class TestGetUserThreads:
    """Tests for getting user's threads."""

    def test_get_user_threads(self, server_with_channel):
        """Test getting threads user has joined."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="User Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="User Thread 2"
        )

        user_threads = threads.get_user_threads(owner.id)

        assert len(user_threads) >= 2
        thread_ids = [t.id for t in user_threads]
        assert thread1.id in thread_ids
        assert thread2.id in thread_ids

    def test_get_user_threads_excludes_archived_by_default(self, server_with_channel):
        """Test that archived threads are excluded by default."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active User Thread"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived User Thread"
        )

        threads.archive_thread(owner.id, thread2.id)

        user_threads = threads.get_user_threads(owner.id)

        thread_ids = [t.id for t in user_threads]
        assert thread1.id in thread_ids
        assert thread2.id not in thread_ids

    def test_get_user_threads_include_archived(self, server_with_channel):
        """Test getting user threads including archived."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active User Thread"
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archived User Thread"
        )

        threads.archive_thread(owner.id, thread2.id)

        user_threads = threads.get_user_threads(owner.id, include_archived=True)

        thread_ids = [t.id for t in user_threads]
        assert thread1.id in thread_ids
        assert thread2.id in thread_ids

    def test_get_user_threads_only_joined(self, server_with_channel):
        """Test that only joined threads are returned."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Owner Thread"
        )

        thread2 = threads.create_thread(
            user_id=member1.id,
            channel_id=channel.id,
            name="Member Thread"
        )

        owner_threads = threads.get_user_threads(owner.id)
        member_threads = threads.get_user_threads(member1.id)

        owner_ids = [t.id for t in owner_threads]
        member_ids = [t.id for t in member_threads]

        assert thread1.id in owner_ids
        assert thread2.id not in owner_ids
        assert thread2.id in member_ids
        assert thread1.id not in member_ids


class TestGetUserPrivateThreads:
    """Tests for getting user's private threads."""

    def test_get_user_private_threads(self, server_with_channel):
        """Test getting user's private threads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        public_thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Public Thread"
        )

        private_thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Thread",
            thread_type=ThreadType.PRIVATE
        )

        private_threads = threads.get_user_private_threads(owner.id)

        thread_ids = [t.id for t in private_threads]
        assert private_thread.id in thread_ids
        assert public_thread.id not in thread_ids

    def test_get_user_private_threads_by_channel(self, server_with_channel):
        """Test getting user's private threads filtered by channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        channel2 = servers.create_channel(
            owner.id, server.id, "channel2",
            channel_type=servers.ChannelType.TEXT
        )

        thread1 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Thread 1",
            thread_type=ThreadType.PRIVATE
        )

        thread2 = threads.create_thread(
            user_id=owner.id,
            channel_id=channel2.id,
            name="Private Thread 2",
            thread_type=ThreadType.PRIVATE
        )

        private_threads = threads.get_user_private_threads(owner.id, channel_id=channel.id)

        thread_ids = [t.id for t in private_threads]
        assert thread1.id in thread_ids
        assert thread2.id not in thread_ids

    def test_get_user_private_threads_empty(self, server_with_channel):
        """Test getting private threads when user has none."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Public Thread"
        )

        private_threads = threads.get_user_private_threads(member1.id)
        assert len(private_threads) == 0
