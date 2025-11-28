"""
Tests for thread membership functionality.
"""

import pytest
from src.core.threads import (
    ThreadType,
    ThreadMemberExistsError,
    ThreadMemberNotFoundError,
    ThreadAccessDeniedError,
    ThreadNotFoundError,
    PermissionDeniedError,
)


class TestJoinThread:
    """Tests for joining threads."""

    def test_join_public_thread(self, server_with_channel):
        """Test joining a public thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Join Test"
        )

        member = threads.join_thread(member1.id, thread.id)

        assert member is not None
        assert member.user_id == member1.id
        assert member.thread_id == thread.id

    def test_join_thread_updates_member_count(self, server_with_channel):
        """Test that joining updates member count."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Count Test"
        )

        assert thread.member_count == 1

        threads.join_thread(member1.id, thread.id)
        updated = threads.get_thread(owner.id, thread.id)

        assert updated.member_count == 2

    def test_join_thread_already_member_fails(self, server_with_channel):
        """Test that joining when already member fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Already Member Test"
        )

        with pytest.raises(ThreadMemberExistsError):
            threads.join_thread(owner.id, thread.id)

    def test_join_nonexistent_thread_fails(self, server_with_channel):
        """Test that joining nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.join_thread(member1.id, 999999999)


class TestLeaveThread:
    """Tests for leaving threads."""

    def test_leave_thread(self, server_with_channel):
        """Test leaving a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Leave Test"
        )

        threads.join_thread(member1.id, thread.id)
        result = threads.leave_thread(member1.id, thread.id)

        assert result is True

    def test_leave_thread_updates_member_count(self, server_with_channel):
        """Test that leaving updates member count."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Leave Count Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.leave_thread(member1.id, thread.id)

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.member_count == 1

    def test_leave_thread_not_member_fails(self, server_with_channel):
        """Test that leaving when not member fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Not Member Test"
        )

        with pytest.raises(ThreadMemberNotFoundError):
            threads.leave_thread(member1.id, thread.id)

    def test_owner_can_leave_thread(self, server_with_channel):
        """Test that owner can leave their own thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Owner Leave Test"
        )

        result = threads.leave_thread(owner.id, thread.id)
        assert result is True


class TestAddMember:
    """Tests for adding members to threads."""

    def test_add_member_to_thread(self, server_with_channel):
        """Test adding a member to a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Add Member Test"
        )

        member = threads.add_member(owner.id, thread.id, member1.id)

        assert member is not None
        assert member.user_id == member1.id

    def test_add_member_already_member_fails(self, server_with_channel):
        """Test that adding existing member fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Already Member Add Test"
        )

        threads.add_member(owner.id, thread.id, member1.id)

        with pytest.raises(ThreadMemberExistsError):
            threads.add_member(owner.id, thread.id, member1.id)

    def test_add_member_not_member_fails(self, server_with_channel):
        """Test that non-member cannot add others."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Non Member Add Test"
        )

        with pytest.raises(ThreadAccessDeniedError):
            threads.add_member(member1.id, thread.id, member2.id)

    def test_member_can_add_to_public_thread(self, server_with_channel):
        """Test that member can add others to public thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Member Add Public Test"
        )

        threads.join_thread(member1.id, thread.id)
        member = threads.add_member(member1.id, thread.id, member2.id)

        assert member.user_id == member2.id


class TestRemoveMember:
    """Tests for removing members from threads."""

    def test_remove_member_from_thread(self, server_with_channel):
        """Test removing a member from a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Remove Member Test"
        )

        threads.add_member(owner.id, thread.id, member1.id)
        result = threads.remove_member(owner.id, thread.id, member1.id)

        assert result is True

    def test_remove_nonmember_fails(self, server_with_channel):
        """Test that removing non-member fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Remove Non Member Test"
        )

        with pytest.raises(ThreadMemberNotFoundError):
            threads.remove_member(owner.id, thread.id, member1.id)

    def test_member_can_remove_self(self, server_with_channel):
        """Test that member can remove themselves."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Self Remove Test"
        )

        threads.join_thread(member1.id, thread.id)
        result = threads.remove_member(member1.id, thread.id, member1.id)

        assert result is True

    def test_cannot_remove_owner_without_permission(self, server_with_channel):
        """Test that non-owner cannot remove owner."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Remove Owner Test"
        )

        threads.join_thread(member1.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.remove_member(member1.id, thread.id, owner.id)


class TestGetThreadMembers:
    """Tests for getting thread members."""

    def test_get_thread_members(self, server_with_channel):
        """Test getting thread members."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Get Members Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.join_thread(member2.id, thread.id)

        members = threads.get_thread_members(owner.id, thread.id)

        assert len(members) == 3
        user_ids = [m.user_id for m in members]
        assert owner.id in user_ids
        assert member1.id in user_ids
        assert member2.id in user_ids

    def test_get_thread_members_pagination(self, server_with_channel):
        """Test getting thread members with pagination."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Pagination Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.join_thread(member2.id, thread.id)

        members = threads.get_thread_members(owner.id, thread.id, limit=2)
        assert len(members) <= 2

    def test_get_thread_members_empty_thread(self, server_with_channel):
        """Test getting members from thread with only owner."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Empty Thread Test"
        )

        members = threads.get_thread_members(owner.id, thread.id)
        assert len(members) == 1
        assert members[0].user_id == owner.id
