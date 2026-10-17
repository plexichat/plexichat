"""
Tests for thread creation functionality.
"""

import pytest
from src.core.threads import (
    ThreadType,
    AutoArchiveDuration,
    ThreadState,
    ThreadNameError,
    ChannelNotFoundError,
)


class TestCreateThread:
    """Tests for creating threads without parent message."""

    def test_create_public_thread(self, server_with_channel):
        """Test creating a public thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Test Thread"
        )

        assert thread is not None
        assert thread.name == "Test Thread"
        assert thread.channel_id == channel.id
        assert thread.server_id == server.id
        assert thread.owner_id == owner.id
        assert thread.thread_type == ThreadType.PUBLIC
        assert thread.state == ThreadState.ACTIVE
        assert thread.parent_message_id is None
        assert thread.member_count == 1
        assert thread.message_count == 0

    def test_create_thread_with_auto_archive(self, server_with_channel):
        """Test creating thread with custom auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Auto Archive Thread",
            auto_archive_duration=AutoArchiveDuration.THREE_DAYS,
        )

        assert thread.auto_archive_duration == AutoArchiveDuration.THREE_DAYS

    def test_create_private_thread(self, server_with_channel):
        """Test creating a private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Thread",
            thread_type=ThreadType.PRIVATE,
        )

        assert thread.thread_type == ThreadType.PRIVATE

    def test_create_announcement_thread(self, server_with_channel):
        """Test creating an announcement thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Announcement Thread",
            thread_type=ThreadType.ANNOUNCEMENT,
        )

        assert thread.thread_type == ThreadType.ANNOUNCEMENT

    def test_create_thread_empty_name_fails(self, server_with_channel):
        """Test that empty thread name fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(user_id=owner.id, channel_id=channel.id, name="")

    def test_create_thread_whitespace_name_fails(self, server_with_channel):
        """Test that whitespace-only thread name fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(user_id=owner.id, channel_id=channel.id, name="   ")

    def test_create_thread_long_name_fails(self, server_with_channel):
        """Test that thread name over 100 chars fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNameError):
            threads.create_thread(
                user_id=owner.id, channel_id=channel.id, name="x" * 101
            )

    def test_create_thread_max_length_name(self, server_with_channel):
        """Test creating thread with exactly 100 char name."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="x" * 100
        )

        assert len(thread.name) == 100

    def test_create_thread_invalid_channel_fails(self, server_with_channel):
        """Test that invalid channel ID fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ChannelNotFoundError):
            threads.create_thread(
                user_id=owner.id, channel_id=999999999, name="Test Thread"
            )

    def test_create_thread_auto_joins_creator(self, server_with_channel):
        """Test that creator is auto-joined to thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Auto Join Test"
        )

        members = threads.get_thread_members(owner.id, thread.id)
        assert len(members) == 1
        assert members[0].user_id == owner.id

    def test_create_thread_by_member(self, server_with_channel):
        """Test that server member can create thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id, channel_id=channel.id, name="Member Thread"
        )

        assert thread.owner_id == member1.id

    def test_create_multiple_threads_same_channel(self, server_with_channel):
        """Test creating multiple threads in same channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread1 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread 1"
        )

        thread2 = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Thread 2"
        )

        assert thread1.id != thread2.id
        assert thread1.name == "Thread 1"
        assert thread2.name == "Thread 2"

    def test_create_thread_name_trimmed(self, server_with_channel):
        """Test that thread name is trimmed."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="  Trimmed Name  "
        )

        assert thread.name == "Trimmed Name"
