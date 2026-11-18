"""Regressions for thread-membership authorization and pagination.

- A negative ``limit`` must not turn the member listing into an unbounded
  SELECT (SQLite treats ``LIMIT -1`` as *unlimited*).
- A server moderator with ``threads.manage`` but who is NOT a member of a
  private thread must not be able to strip that thread's membership. Private
  thread audiences are inviolate to non-members, and ``add_member`` already
  requires caller membership for the same reason.
"""

import pytest

from src.core.threads import ThreadType, ThreadAccessDeniedError


class TestThreadMemberLimitClamp:
    def test_negative_limit_returns_empty_page(self, server_with_channel):
        owner, member1, _member2, _server, channel, _servers, threads = (
            server_with_channel
        )
        thread = threads.create_thread(owner.id, channel.id, "Limit Clamp")
        threads.add_member(owner.id, thread.id, member1.id)
        assert len(threads.get_thread_members(owner.id, thread.id)) == 2

        # SQLite treats LIMIT -1 as unlimited; the manager must clamp it.
        assert threads.get_thread_members(owner.id, thread.id, limit=-1) == []

    def test_zero_limit_returns_empty_page(self, server_with_channel):
        owner, member1, _member2, _server, channel, _servers, threads = (
            server_with_channel
        )
        thread = threads.create_thread(owner.id, channel.id, "Zero Limit")
        threads.add_member(owner.id, thread.id, member1.id)

        assert threads.get_thread_members(owner.id, thread.id, limit=0) == []

    def test_positive_limit_is_honoured(self, server_with_channel):
        owner, member1, _member2, _server, channel, _servers, threads = (
            server_with_channel
        )
        thread = threads.create_thread(owner.id, channel.id, "Positive Limit")
        threads.add_member(owner.id, thread.id, member1.id)

        members = threads.get_thread_members(owner.id, thread.id, limit=1)
        assert len(members) == 1


class TestPrivateThreadMemberRemovalAuthz:
    def test_non_member_moderator_cannot_remove_private_member(
        self, server_with_moderator
    ):
        owner, moderator, member, _server, channel, _servers, threads = (
            server_with_moderator
        )
        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Mod Removal",
            thread_type=ThreadType.PRIVATE,
        )
        threads.add_member(owner.id, thread.id, member.id)
        assert threads.can_manage_thread(moderator.id, thread.id) is True
        assert threads._is_member(moderator.id, thread.id) is False

        with pytest.raises(ThreadAccessDeniedError):
            threads.remove_member(moderator.id, thread.id, member.id)

        assert threads._is_member(member.id, thread.id) is True

    def test_member_moderator_can_still_remove_private_member(
        self, server_with_moderator
    ):
        owner, moderator, member, _server, channel, _servers, threads = (
            server_with_moderator
        )
        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private Mod In Thread",
            thread_type=ThreadType.PRIVATE,
        )
        threads.add_member(owner.id, thread.id, member.id)
        threads.add_member(owner.id, thread.id, moderator.id)

        assert threads.remove_member(moderator.id, thread.id, member.id) is True
        assert threads._is_member(member.id, thread.id) is False
