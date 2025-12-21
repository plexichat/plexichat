"""
Tests for thread message functionality.
"""

import pytest
from src.core.threads import (
    ThreadLockedError,
    ThreadAccessDeniedError,
    ThreadNotFoundError,
    ThreadState,
)


class TestSendMessage:
    """Tests for sending messages to threads."""

    def test_send_message_to_thread(self, server_with_channel):
        """Test sending a message to a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Message Test"
        )

        msg = threads.send_message(owner.id, thread.id, "Hello thread!")

        assert msg is not None
        assert msg["content"] == "Hello thread!"
        assert msg["user_id"] == owner.id
        assert msg["thread_id"] == thread.id

    def test_send_message_updates_count(self, server_with_channel):
        """Test that sending message updates message count."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Count Update Test"
        )

        assert thread.message_count == 0

        threads.send_message(owner.id, thread.id, "Message 1")
        threads.send_message(owner.id, thread.id, "Message 2")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.message_count == 2

    def test_send_message_updates_last_message_at(self, server_with_channel):
        """Test that sending message updates last_message_at."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Last Message Test"
        )

        assert thread.last_message_at is None

        threads.send_message(owner.id, thread.id, "Test message")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.last_message_at is not None

    def test_send_message_auto_joins_user(self, server_with_channel):
        """Test that sending message auto-joins user to thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Auto Join Message Test"
        )

        threads.send_message(member1.id, thread.id, "Hello!")

        members = threads.get_thread_members(owner.id, thread.id)
        user_ids = [m.user_id for m in members]
        assert member1.id in user_ids

    def test_send_message_to_locked_thread_fails(self, server_with_channel):
        """Test that sending to locked thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Locked Thread Test"
        )

        threads.lock_thread(owner.id, thread.id)

        with pytest.raises(ThreadLockedError):
            threads.send_message(member1.id, thread.id, "Should fail")

    def test_send_message_unarchives_thread(self, server_with_channel):
        """Test that sending message unarchives thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Unarchive Test"
        )

        threads.archive_thread(owner.id, thread.id)
        archived = threads.get_thread(owner.id, thread.id)
        assert archived.state == ThreadState.ARCHIVED

        threads.send_message(owner.id, thread.id, "Unarchive message")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.state == ThreadState.ACTIVE

    def test_send_message_nonexistent_thread_fails(self, server_with_channel):
        """Test that sending to nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.send_message(owner.id, 999999999, "Should fail")


class TestGetMessages:
    """Tests for getting messages from threads."""

    def test_get_messages(self, server_with_channel):
        """Test getting messages from a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Get Messages Test"
        )

        threads.send_message(owner.id, thread.id, "Message 1")
        threads.send_message(owner.id, thread.id, "Message 2")
        threads.send_message(owner.id, thread.id, "Message 3")

        messages = threads.get_messages(owner.id, thread.id)

        assert len(messages) == 3

    def test_get_messages_with_limit(self, server_with_channel):
        """Test getting messages with limit."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Limit Test"
        )

        for i in range(10):
            threads.send_message(owner.id, thread.id, f"Message {i}")

        messages = threads.get_messages(owner.id, thread.id, limit=5)

        assert len(messages) == 5

    def test_get_messages_empty_thread(self, server_with_channel):
        """Test getting messages from empty thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Empty Messages Test"
        )

        messages = threads.get_messages(owner.id, thread.id)

        assert len(messages) == 0

    def test_get_messages_access_denied(self, server_with_channel):
        """Test that non-member cannot get private thread messages."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Messages Test",
            thread_type=threads.ThreadType.PRIVATE
        )

        threads.send_message(owner.id, thread.id, "Secret message")

        with pytest.raises(ThreadAccessDeniedError):
            threads.get_messages(member1.id, thread.id)


class TestGetMessageCount:
    """Tests for getting message count."""

    def test_get_message_count(self, server_with_channel):
        """Test getting message count."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Message Count Test"
        )

        assert threads.get_message_count(thread.id) == 0

        threads.send_message(owner.id, thread.id, "Message 1")
        threads.send_message(owner.id, thread.id, "Message 2")

        assert threads.get_message_count(thread.id) == 2

    def test_get_message_count_nonexistent_thread(self, server_with_channel):
        """Test getting message count for nonexistent thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        count = threads.get_message_count(999999999)
        assert count == 0
