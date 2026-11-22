"""Regression: the ``suppress_everyone`` audience for a role ping must use the
same author-excluding everyone-equivalent verdict as the permission gate.

N783 made the *gate* (``validate_mentions``) classify a role held by every
member EXCEPT the pinger as an ``@everyone`` equivalent, because the fan-out
skips the author.  The recipient-side suppression set (``mass_ping_recipients``
in ``create_notifications``) used a separate, author-UN-aware coverage count,
so a member who opted out of ``@everyone`` still received the content preview
of an all-except-author role mass ping.  This module pins the two decisions in
lockstep: an opted-out member must be suppressed by exactly the same role that
needs ``messages.mention_everyone`` at the gate.
"""

import pytest


@pytest.fixture
def wired(users_with_server):
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


def _bodies(notifs, marker="BODY-MARKER"):
    return {
        n.user_id: n.content_preview or ""
        for n in notifs
        if marker in (n.content_preview or "")
    }


def test_all_except_author_role_respects_suppress_everyone(wired):
    """An all-except-author role ping must honour the recipient's opt-out.

    The owner (author) does not hold the role; member/member2 do.  The fan-out
    therefore pings exactly the ``@everyone`` audience and the
    ``suppress_everyone`` member must not see the content preview.
    """
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="everyone-but-author",
        permissions={},
        mentionable=True,
    )
    for uid in (member.id, member2.id):
        servers.assign_role(owner.id, server.id, uid, role.id)

    content = f"BODY-MARKER <@&{role.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    bodies = _bodies(notifs)
    assert member.id not in bodies, (
        "suppress_everyone member received an all-except-author role mass ping"
    )
    assert member2.id in bodies, "unsuppressed member must still be notified"


def test_ordinary_partial_role_not_treated_as_mass_ping(wired):
    """Control: a role held by only some members is not an @everyone equivalent.

    ``suppress_everyone`` does not cover ordinary role pings (that is what
    ``suppress_roles`` is for), so the opt-out member must still be notified.
    """
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    notifications.update_notification_settings(
        member.id, server.id, suppress_everyone=True
    )

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="partial-team",
        permissions={},
        mentionable=True,
    )
    servers.assign_role(owner.id, server.id, member.id, role.id)

    content = f"BODY-MARKER <@&{role.id}>"
    notifs = _fanout(messaging, notifications, owner, channel, server, content)

    assert member.id in _bodies(notifs)
