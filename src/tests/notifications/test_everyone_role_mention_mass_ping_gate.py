"""Regression: mentioning the server's default ``@everyone`` role is an
@everyone-equivalent mass ping and must clear the SAME fail-closed
``messages.mention_everyone`` gate as a literal ``@everyone``/``@here`` (and as
a self-channel ``<#this-channel>`` mention).

``NotificationCreatorMixin.create_notifications`` fans a ROLE mention out to
every member of that role (``get_role_members``). Every member holds the
server's default ``is_default = 1`` (``@everyone``) role, so ``<@&default_role>``
reaches the entire server -- it bumps mention counts and queues push exactly
like ``@everyone``. But ``validate_mentions`` only checked the role's
``mentionable`` flag / ``roles.manage`` for ROLE mentions and never consulted
``has_mention_everyone_permission``. Since the owner may set the default role
``mentionable = True`` (``update_role`` allows that for the owner), *any*
member could then mass-ping the whole server while lacking
``messages.mention_everyone``.

The fix routes a ROLE mention whose target is the default ``@everyone`` role
through the same single audited mass-mention gate. These tests prove the gate
actually denies (no notifications created without the permission) and that a
legitimate with-permission fan-out and an ordinary mentionable role are
unaffected (so the fix is not a blanket drop of role mentions).
"""

import pytest

from src.core.notifications.models import MentionType


@pytest.fixture
def wired(users_with_server):
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        users_with_server
    )
    notifications._servers = servers
    # The owner makes the default @everyone role mentionable. This is a legal
    # owner action; it must NOT turn the @everyone role into a permission-free
    # mass-ping primitive for every other member.
    servers.update_role(owner.id, server.default_role_id, mentionable=True)
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


def test_everyone_role_mention_denied_without_mention_everyone(wired):
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired

    assert not servers.has_permission(
        member.id, server.id, "messages.mention_everyone", channel.id
    )

    content = f"ping <@&{server.default_role_id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert len(validated) == 1
    assert validated[0].mention_type is MentionType.ROLE
    assert validated[0].valid is False
    assert "permission" in validated[0].error.lower()

    # The decisive assertion: without the permission, NO mass ping is created.
    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert notifs == []


def test_everyone_role_mention_allowed_with_mention_everyone(wired):
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired

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

    content = f"ping <@&{server.default_role_id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert validated[0].valid is True

    # With the permission the legitimate whole-server fan-out still happens.
    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert any(n.mention_type is MentionType.ROLE for n in notifs)


def test_ordinary_mentionable_role_still_allowed(wired):
    """A normal (non-@everyone) mentionable role is still pingable by anyone."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="team",
        permissions={},
        mentionable=True,
    )
    servers.assign_role(owner.id, server.id, member2.id, role.id)

    content = f"ping <@&{role.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert validated[0].valid is True

    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert any(n.mention_type is MentionType.ROLE for n in notifs)
