"""
Tests for private thread functionality.
"""

import pytest
from src.core.threads import (
    ThreadType,
    ThreadAccessDeniedError,
    PermissionDeniedError,
)


class TestPrivateThreadCreation:
    """Tests for creating private threads."""

    def test_create_private_thread(self, server_with_channel):
        """Test creating a private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Thread",
            thread_type=ThreadType.PRIVATE
        )

        assert thread.thread_type == ThreadType.PRIVATE

    def test_private_thread_creator_is_member(self, server_with_channel):
        """Test that creator is automatically a member."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Creator Test",
            thread_type=ThreadType.PRIVATE
        )

        members = threads.get_thread_members(owner.id, thread.id)
        assert len(members) == 1
        assert members[0].user_id == owner.id


class TestPrivateThreadVisibility:
    """Tests for private thread visibility."""

    def test_private_thread_not_visible_to_non_members(self, server_with_channel):
        """Test that private thread is not visible to non-members."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Hidden Thread",
            thread_type=ThreadType.PRIVATE
        )

        result = threads.get_thread(member1.id, thread.id)
        assert result is None

    def test_private_thread_visible_to_members(self, server_with_channel):
        """Test that private thread is visible to members."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Visible Thread",
            thread_type=ThreadType.PRIVATE
        )

        threads.add_member(owner.id, thread.id, member1.id)

        result = threads.get_thread(member1.id, thread.id)
        assert result is not None
        assert result.id == thread.id

    def test_private_thread_not_in_active_threads_for_non_member(self, server_with_channel):
        """Test that private thread not in active threads for non-member."""
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

        active = threads.get_active_threads(member1.id, channel.id)
        thread_ids = [t.id for t in active]

        assert public_thread.id in thread_ids
        assert private_thread.id not in thread_ids


class TestPrivateThreadMembership:
    """Tests for private thread membership."""

    def test_cannot_join_private_thread_directly(self, server_with_channel):
        """Test that users cannot join private thread directly."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="No Join Thread",
            thread_type=ThreadType.PRIVATE
        )

        with pytest.raises(ThreadAccessDeniedError):
            threads.join_thread(member1.id, thread.id)

    def test_owner_can_add_members_to_private_thread(self, server_with_channel):
        """Test that owner can add members to private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Add Member Thread",
            thread_type=ThreadType.PRIVATE
        )

        member = threads.add_member(owner.id, thread.id, member1.id)
        assert member.user_id == member1.id

    def test_member_cannot_add_to_private_thread_without_permission(self, server_with_channel):
        """Test that regular member cannot add others to private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="No Add Thread",
            thread_type=ThreadType.PRIVATE
        )

        threads.add_member(owner.id, thread.id, member1.id)

        with pytest.raises(PermissionDeniedError):
            threads.add_member(member1.id, thread.id, member2.id)

    def test_member_can_leave_private_thread(self, server_with_channel):
        """Test that member can leave private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Leave Thread",
            thread_type=ThreadType.PRIVATE
        )

        threads.add_member(owner.id, thread.id, member1.id)
        result = threads.leave_thread(member1.id, thread.id)

        assert result is True


class TestPrivateThreadMessages:
    """Tests for private thread messages."""

    def test_non_member_cannot_get_messages(self, server_with_channel):
        """Test that non-member cannot get private thread messages."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Secret Messages",
            thread_type=ThreadType.PRIVATE
        )

        threads.send_message(owner.id, thread.id, "Secret message")

        with pytest.raises(ThreadAccessDeniedError):
            threads.get_messages(member1.id, thread.id)

    def test_member_can_get_messages(self, server_with_channel):
        """Test that member can get private thread messages."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Member Messages",
            thread_type=ThreadType.PRIVATE
        )

        threads.add_member(owner.id, thread.id, member1.id)
        threads.send_message(owner.id, thread.id, "Visible message")

        messages = threads.get_messages(member1.id, thread.id)
        assert len(messages) == 1

    def test_non_member_cannot_send_message(self, server_with_channel):
        """Test that non-member cannot send to private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="No Send Thread",
            thread_type=ThreadType.PRIVATE
        )

        with pytest.raises(ThreadAccessDeniedError):
            threads.send_message(member1.id, thread.id, "Should fail")

    def test_member_can_send_message(self, server_with_channel):
        """Test that member can send to private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Send Thread",
            thread_type=ThreadType.PRIVATE
        )

        threads.add_member(owner.id, thread.id, member1.id)
        msg = threads.send_message(member1.id, thread.id, "Hello!")

        assert msg is not None
