"""Regression: a self-channel mention is an @everyone-equivalent mass ping.

``NotificationCreatorMixin.create_notifications`` fans a CHANNEL mention whose
target is the message's own channel out to *every* member who can view that
channel (as ``MentionType.CHANNEL``) -- it bumps mention counts and queues push
just like ``@everyone``/``@here``. ``validate_mentions`` used to check only
``channel_exists`` for channel mentions, so any member who could send a message
could mass-ping the whole channel with ``<#the-channel-they-are-in>`` while
lacking ``messages.mention_everyone``.

The fix applies the same fail-closed mass-mention permission gate to a mention
of the current channel. These tests prove the gate actually denies (not just
validate-returns-invalid) by asserting that NO notifications are created, and
that a legitimate with-permission fan-out and DM/non-self channel links remain
unaffected (so the fix is not a blanket drop of channel mentions).
"""

import pytest

from src.core.notifications.models import MentionType


@pytest.fixture
def wired_notifications(users_with_server):
    """A server with the notifications <-> servers wiring production uses."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        users_with_server
    )
    notifications._servers = servers
    return (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    )


def _fanout(messaging, notifications, author, channel, server, content):
    msg = messaging.send_message(author.id, channel.conversation_id, content)
    return notifications.create_notifications_for_message(
        author_id=author.id,
        message_id=msg.id,
        conversation_id=channel.conversation_id,
        content=content,
        server_id=server.id,
        channel_id=channel.id,
    )


def test_self_channel_mention_denied_without_mention_everyone(wired_notifications):
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        wired_notifications
    )

    assert not servers.has_permission(
        member.id, server.id, "messages.mention_everyone", channel.id
    )

    content = f"ping <#{channel.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert len(validated) == 1
    assert validated[0].mention_type is MentionType.CHANNEL
    assert validated[0].valid is False
    assert "permission" in validated[0].error.lower()

    # The decisive assertion: no fan-out, so nobody is actually pinged.
    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert notifs == []


def test_self_channel_mention_allowed_with_mention_everyone(wired_notifications):
    """A member WITH the permission still reaches the whole-channel fan-out."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        wired_notifications
    )

    # Grant the permission channel-scoped on this channel to the plain member.
    # (The owner holds channels.manage and can set member overrides.)
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        member.id,
        allow={"messages.mention_everyone": True},
    )
    assert servers.has_permission(
        member.id, server.id, "messages.mention_everyone", channel.id
    )

    content = f"ping <#{channel.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert validated[0].valid is True

    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert any(n.mention_type is MentionType.CHANNEL for n in notifs)


def test_other_channel_link_still_allowed(wired_notifications):
    """A link to a DIFFERENT channel is not a mass ping and stays valid."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        wired_notifications
    )

    other = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="other",
        channel_type=servers.ChannelType.TEXT,
    )
    content = f"see <#{other.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert validated[0].valid is True
