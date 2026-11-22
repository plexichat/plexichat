"""Regression: @everyone-equivalent mass pings must honour ``suppress_everyone``.

``validate_mentions`` gates a literal ``@everyone``, a mention of the current
channel (``<#this-channel>``) and the default ``@everyone`` role behind the
same ``messages.mention_everyone`` permission.  The recipient-side preference
was only ever applied to mentions whose ``MentionType`` is EVERYONE/HERE:
``_should_notify_user_optimized`` checks ``settings.suppress_everyone`` for
those two types alone.  A self-channel ping is typed CHANNEL and a default-role
ping is typed ROLE, so a member who explicitly opted out of mass mentions
still received the whole-channel/server notification -- including the message
content preview in both the websocket event and the device push payload.

The fix routes these two @everyone-equivalent fan-outs through the same
``suppress_everyone`` check.  Positive controls keep ordinary channel links,
ordinary role mentions and users who did not opt out unaffected.
"""

import pytest


@pytest.fixture
def wired(users_with_server):
    (owner, member, member2, server, channel, servers, messaging, notifications) = (
        users_with_server
    )
    # The notification manager in this fixture is wired directly so the
    # channel-view recipient filter can evaluate permissions.
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


def _body_marker(uid, msg):
    return any(
        n.user_id == uid and "SUPPRESSED-BODY-MARKER" in (n.content_preview or "")
        for n in msg
    )


def test_literal_everyone_is_suppressed_control(wired):
    """Control: a literal @everyone is already suppressed correctly."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )

    notifs = _fanout(
        messaging,
        notifications,
        owner,
        channel,
        server,
        "SUPPRESSED-BODY-MARKER @everyone",
    )
    assert not _body_marker(member.id, notifs)
    assert _body_marker(member2.id, notifs), "unsuppressed member still notified"


def test_self_channel_mass_ping_respects_suppress_everyone(wired):
    """<#this-channel> is an @everyone equivalent; opt-out must still apply."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )

    content = f"SUPPRESSED-BODY-MARKER <#{channel.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert not _body_marker(member.id, notifs), (
        "a member who suppressed @everyone received the content preview of "
        "a whole-channel <#this-channel> mass ping"
    )
    assert _body_marker(member2.id, notifs), "unsuppressed member still notified"


def test_default_role_mass_ping_respects_suppress_everyone(wired):
    """<@&default_role> is an @everyone equivalent; opt-out must still apply."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )

    content = f"SUPPRESSED-BODY-MARKER <@&{server.default_role_id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert not _body_marker(member.id, notifs), (
        "a member who suppressed @everyone received the content preview of "
        "a default-role mass ping"
    )
    assert _body_marker(member2.id, notifs), "unsuppressed member still notified"


def test_ordinary_role_mention_still_delivered(wired):
    """A non-default mentionable role is NOT an @everyone equivalent."""
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="team",
        permissions={},
        mentionable=True,
    )
    servers.assign_role(owner.id, server.id, member.id, role.id)

    content = f"BODY-MARKER <@&{role.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    # suppress_everyone does not cover ordinary role pings; suppress_roles does.
    assert any(
        n.user_id == member.id and "BODY-MARKER" in (n.content_preview or "")
        for n in notifs
    )
