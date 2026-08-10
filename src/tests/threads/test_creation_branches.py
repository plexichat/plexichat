"""
Branch coverage for thread creation.

Covers the permission gates on private threads and the previously
untested ``create_thread_from_message`` flow (message lookup, duplicate
guard, conversation-metadata channel resolution, validation errors).
"""

import json

import pytest

from src.core.threads import (
    ThreadType,
    ThreadNameError,
    MessageNotFoundError,
    ThreadError,
    PermissionDeniedError,
)


def _message_context(threads, owner, member, channel):
    """Create a conversation + message wired to a server channel.

    Returns (conversation, message) with the conversation's metadata
    pointing at ``channel.id`` so the from-message flow can resolve the
    channel the message belongs to.
    """
    conv = threads._messaging.create_group(owner.id, "Conv", [member.id])
    msg = threads._messaging.send_message(owner.id, conv.id, "hello")
    threads._db.execute(
        "UPDATE msg_conversations SET metadata = ? WHERE id = ?",
        (json.dumps({"channel_id": channel.id}), conv.id),
    )
    return conv, msg


class TestCreateThreadPermissionGates:
    def test_member_without_private_permission_denied(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(PermissionDeniedError):
            threads.create_thread(
                user_id=member1.id,
                channel_id=channel.id,
                name="Private by member",
                thread_type=ThreadType.PRIVATE,
            )

    def test_member_can_create_public_thread(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id, channel_id=channel.id, name="Public by member"
        )

        assert thread.owner_id == member1.id
        assert thread.thread_type == ThreadType.PUBLIC

    def test_owner_can_create_private_thread(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Private by owner",
            thread_type=ThreadType.PRIVATE,
        )

        assert thread.thread_type == ThreadType.PRIVATE


class TestCreateThreadFromMessage:
    def test_unknown_message_raises(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(MessageNotFoundError):
            threads.create_thread_from_message(owner.id, 999999, "Thread")

    def test_empty_name_raises(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        conv, msg = _message_context(threads, owner, member1, channel)

        with pytest.raises(ThreadNameError):
            threads.create_thread_from_message(owner.id, msg.id, "   ")

    def test_creates_thread_from_message(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        conv, msg = _message_context(threads, owner, member1, channel)

        thread = threads.create_thread_from_message(owner.id, msg.id, "From Message")

        assert thread.parent_message_id == msg.id
        assert thread.channel_id == channel.id
        assert thread.server_id == server.id
        assert thread.owner_id == owner.id
        assert thread.name == "From Message"
        assert thread.member_count == 1

    def test_duplicate_thread_for_message_raises(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        conv, msg = _message_context(threads, owner, member1, channel)

        threads.create_thread_from_message(owner.id, msg.id, "First")
        with pytest.raises(ThreadError, match="already exists"):
            threads.create_thread_from_message(owner.id, msg.id, "Second")

    def test_missing_conversation_metadata_raises(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        conv = threads._messaging.create_group(owner.id, "Conv", [member1.id])
        msg = threads._messaging.send_message(owner.id, conv.id, "hello")
        # No metadata: the flow cannot resolve the channel.

        with pytest.raises(ThreadError, match="channel_id"):
            threads.create_thread_from_message(owner.id, msg.id, "Thread")
