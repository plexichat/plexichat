"""
Tests for thread permission functionality.
"""

import pytest
from src.core.threads import (
    ThreadType,
    PermissionDeniedError,
)


class TestCanViewThread:
    """Tests for can_view_thread permission check."""

    def test_can_view_public_thread(self, server_with_channel):
        """Test that anyone can view public thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Public View Test"
        )

        assert threads.can_view_thread(owner.id, thread.id) is True
        assert threads.can_view_thread(member1.id, thread.id) is True
        assert threads.can_view_thread(member2.id, thread.id) is True

    def test_can_view_private_thread_member_only(self, server_with_channel):
        """Test that only members can view private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private View Test",
            thread_type=ThreadType.PRIVATE
        )

        assert threads.can_view_thread(owner.id, thread.id) is True
        assert threads.can_view_thread(member1.id, thread.id) is False

        threads.add_member(owner.id, thread.id, member1.id)
        assert threads.can_view_thread(member1.id, thread.id) is True

    def test_can_view_nonexistent_thread(self, server_with_channel):
        """Test viewing nonexistent thread returns False."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        assert threads.can_view_thread(owner.id, 999999999) is False


class TestCanSendInThread:
    """Tests for can_send_in_thread permission check."""

    def test_can_send_in_public_thread(self, server_with_channel):
        """Test that channel members can send in public thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Public Send Test"
        )

        assert threads.can_send_in_thread(owner.id, thread.id) is True
        assert threads.can_send_in_thread(member1.id, thread.id) is True

    def test_cannot_send_in_locked_thread(self, server_with_channel):
        """Test that non-managers cannot send in locked thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Locked Send Test"
        )

        threads.lock_thread(owner.id, thread.id)

        assert threads.can_send_in_thread(member1.id, thread.id) is False
        assert threads.can_send_in_thread(owner.id, thread.id) is True

    def test_cannot_send_in_private_thread_non_member(self, server_with_channel):
        """Test that non-members cannot send in private thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Send Test",
            thread_type=ThreadType.PRIVATE
        )

        assert threads.can_send_in_thread(member1.id, thread.id) is False

    def test_can_send_nonexistent_thread(self, server_with_channel):
        """Test sending in nonexistent thread returns False."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        assert threads.can_send_in_thread(owner.id, 999999999) is False


class TestCanManageThread:
    """Tests for can_manage_thread permission check."""

    def test_owner_can_manage_thread(self, server_with_channel):
        """Test that thread owner can manage thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id,
            channel_id=channel.id,
            name="Owner Manage Test"
        )

        assert threads.can_manage_thread(member1.id, thread.id) is True

    def test_non_owner_cannot_manage_thread(self, server_with_channel):
        """Test that non-owner cannot manage thread without permission."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Non Owner Manage Test"
        )

        assert threads.can_manage_thread(member1.id, thread.id) is False

    def test_moderator_can_manage_thread(self, server_with_moderator):
        """Test that moderator with permission can manage thread."""
        owner, moderator, member, server, channel, servers, threads = server_with_moderator

        thread = threads.create_thread(
            user_id=member.id,
            channel_id=channel.id,
            name="Moderator Manage Test"
        )

        assert threads.can_manage_thread(moderator.id, thread.id) is True

    def test_can_manage_nonexistent_thread(self, server_with_channel):
        """Test managing nonexistent thread returns False."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        assert threads.can_manage_thread(owner.id, 999999999) is False


class TestModeratorPermissions:
    """Tests for moderator thread permissions."""

    def test_moderator_can_archive_any_thread(self, server_with_moderator):
        """Test that moderator can archive any thread."""
        owner, moderator, member, server, channel, servers, threads = server_with_moderator

        thread = threads.create_thread(
            user_id=member.id,
            channel_id=channel.id,
            name="Mod Archive Test"
        )

        archived = threads.archive_thread(moderator.id, thread.id)
        assert archived.state == threads.ThreadState.ARCHIVED

    def test_moderator_can_lock_any_thread(self, server_with_moderator):
        """Test that moderator can lock any thread."""
        owner, moderator, member, server, channel, servers, threads = server_with_moderator

        thread = threads.create_thread(
            user_id=member.id,
            channel_id=channel.id,
            name="Mod Lock Test"
        )

        locked = threads.lock_thread(moderator.id, thread.id)
        assert locked.locked is True

    def test_moderator_can_delete_any_thread(self, server_with_moderator):
        """Test that moderator can delete any thread."""
        owner, moderator, member, server, channel, servers, threads = server_with_moderator

        thread = threads.create_thread(
            user_id=member.id,
            channel_id=channel.id,
            name="Mod Delete Test"
        )

        result = threads.delete_thread(moderator.id, thread.id)
        assert result is True

    def test_moderator_can_update_any_thread(self, server_with_moderator):
        """Test that moderator can update any thread."""
        owner, moderator, member, server, channel, servers, threads = server_with_moderator

        thread = threads.create_thread(
            user_id=member.id,
            channel_id=channel.id,
            name="Mod Update Test"
        )

        updated = threads.update_thread(moderator.id, thread.id, name="Updated Name")
        assert updated.name == "Updated Name"
