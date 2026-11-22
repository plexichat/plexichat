"""Regression: a role ping must honour ``suppress_roles`` for every audience
member, regardless of the MentionType the fan-out finally assigns them.

``validate_mentions`` gates a self-channel mention (``<#this-channel>``) and a
role mention (``<@&role_id>``) independently, and the parser sorts mentions by
position. ``create_notifications`` de-duplicates recipients with a
``if user_id not in mention_types`` guard, so when a message carries BOTH a
self-channel mention and a role mention, the first one in the text wins the
recipient's MentionType. A member of the mentioned role whose opt-out is
``suppress_roles`` is then recorded as CHANNEL (or EVERYONE/HERE/USER) rather
than ROLE, and ``_should_notify_user_optimized`` only consults
``suppress_roles`` for ``MentionType.ROLE``. The opted-out member therefore
receives the whole notification -- including the content preview -- over both
the websocket event and the push payload.

The fix mirrors N670's ``mass_ping_recipients`` approach: track role-ping
audience members by semantic equivalence and apply ``suppress_roles`` to them
in the fan-out loop, not by the raw ``mention_type`` stored for de-duplication.
Positive controls keep ordinary channel links and unopted members unaffected.
"""

import pytest


@pytest.fixture
def wired(users_with_server):
    (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    ) = users_with_server
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


def _body_marker(uid, msg, marker):
    return any(
        n.user_id == uid and marker in (n.content_preview or "") for n in msg
    )


def _make_role(servers, owner, server, *members):
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="team",
        permissions={},
        mentionable=True,
    )
    for member in members:
        servers.assign_role(owner.id, server.id, member.id, role.id)
    return role


def test_plain_role_ping_is_suppressed_control(wired):
    """Control: a lone role mention is already suppressed correctly."""
    (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    ) = wired
    notifications.update_notification_settings(member.id, server.id, suppress_roles=True)
    role = _make_role(servers, owner, server, member, member2)

    notifs = _fanout(
        messaging, notifications, owner, channel, server,
        f"ROLE-MARKER <@&{role.id}>",
    )
    assert not _body_marker(member.id, notifs, "ROLE-MARKER")
    assert _body_marker(member2.id, notifs, "ROLE-MARKER"), (
        "unsuppressed role member still notified"
    )


def test_role_ping_retyped_as_channel_bypasses_suppress_roles(wired):
    """A role mention spelled after a self-channel ping leaks to an opt-out.

    The recipient is a member of the mentioned role and set suppress_roles,
    yet the self-channel mention (which appears first) wins the de-dup and
    records them as MentionType.CHANNEL. suppress_roles is then never applied
    and the full content preview is delivered.
    """
    (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    ) = wired
    notifications.update_notification_settings(member.id, server.id, suppress_roles=True)
    role = _make_role(servers, owner, server, member)

    content = f"ROLE-BYPASS-MARKER <#{channel.id}> <@&{role.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert not _body_marker(member.id, notifs, "ROLE-BYPASS-MARKER"), (
        "a member who suppressed role mentions received the content preview "
        "of a role ping that was retyped as a self-channel mention"
    )


def test_role_ping_retyped_as_everyone_bypasses_suppress_roles(wired):
    """Same bypass with an @everyone mention winning the de-dup."""
    (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    ) = wired
    notifications.update_notification_settings(member.id, server.id, suppress_roles=True)
    role = _make_role(servers, owner, server, member)

    content = f"ROLE-EVERYONE-MARKER @everyone <@&{role.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert not _body_marker(member.id, notifs, "ROLE-EVERYONE-MARKER"), (
        "a member who suppressed role mentions received the content preview "
        "of a role ping that was retyped as an @everyone mention"
    )


def test_role_ping_retyped_as_user_bypasses_suppress_roles(wired):
    """A direct USER mention always wins de-dup; it must not un-suppress a role ping."""
    (
        owner,
        member,
        member2,
        server,
        channel,
        servers,
        messaging,
        notifications,
    ) = wired
    notifications.update_notification_settings(member.id, server.id, suppress_roles=True)
    role = _make_role(servers, owner, server, member)

    content = f"ROLE-USER-MARKER <@&{role.id}> <@{member.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert not _body_marker(member.id, notifs, "ROLE-USER-MARKER"), (
        "a member who suppressed role mentions received the content preview "
        "of a role ping that was retyped as a direct user mention"
    )
