"""Regression: a non-default role held by EVERY server member is an
@everyone-equivalent mass ping and must clear the same fail-closed
``messages.mention_everyone`` gate as ``@everyone``/``@here``, a self-channel
``<#this-channel>`` mention, and the default ``@everyone`` role.

``validate_mentions`` only recognised the default role (``is_default = 1``) as
an @everyone equivalent.  A server owner can create an ordinary role, make it
mentionable and assign it to every member, which produces the exact same fan-
out: ``create_notifications`` resolves ROLE mentions with ``get_role_members``
and pings every holder.  Without this check any member who lacks
``messages.mention_everyone`` could mass-ping the whole server with
``<@&all_members_role>`` -- the same bypass N719 closed for the default role,
just reached through a "recreated" everyone role that does not carry the
``is_default`` flag.

The test is non-vacuous: it asserts the real fan-out creates notifications for
every other member before the fix (the bypass), and the fixed validator marks
the mention invalid and creates none.  A positive control keeps an ordinary
(partially-held) mentionable role pingable.
"""

import pytest

from src.core.notifications.manager.helpers import role_covers_all_members
from src.core.notifications.models import MentionType


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


def test_all_member_role_requires_mention_everyone(wired):
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired

    assert not servers.has_permission(
        member.id, server.id, "messages.mention_everyone", channel.id
    )

    # An ordinary (non-default), mentionable role assigned to EVERY member.
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="all-members",
        permissions={},
        mentionable=True,
    )
    assert not role.is_default
    for uid in (owner.id, member.id, member2.id):
        servers.assign_role(owner.id, server.id, uid, role.id)

    content = f"ping <@&{role.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert len(validated) == 1
    assert validated[0].mention_type is MentionType.ROLE
    assert validated[0].valid is False
    assert "permission" in (validated[0].error or "").lower()

    # Decisive assertion: no mass ping without the permission.
    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert notifs == []


def test_all_member_role_allowed_with_mention_everyone(wired):
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

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="all-members",
        permissions={},
        mentionable=True,
    )
    for uid in (owner.id, member.id, member2.id):
        servers.assign_role(owner.id, server.id, uid, role.id)

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


def test_partially_held_role_still_allowed(wired):
    """Control: a role held by only some members is not an @everyone equivalent."""
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
    assert any(n.user_id == member2.id for n in notifs)


def test_role_covering_all_except_author_requires_mention_everyone(wired):
    """A role held by every member EXCEPT the pinger is an @everyone equivalent.

    ``create_notifications`` skips the author when fanning a ROLE mention out,
    so a role assigned to every other member reaches exactly the @everyone
    audience. A literal "all members" coverage check misses this case (the
    author does not hold the role) and let a member without
    ``messages.mention_everyone`` mass-ping the whole server.
    """
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired

    assert not servers.has_permission(
        member.id, server.id, "messages.mention_everyone", channel.id
    )

    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="everyone-but-the-pinger",
        permissions={},
        mentionable=True,
    )
    assert not role.is_default
    # Every member EXCEPT the would-be pinger holds it.
    for uid in (owner.id, member2.id):
        servers.assign_role(owner.id, server.id, uid, role.id)

    content = f"ping <@&{role.id}>"
    validated = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert len(validated) == 1
    assert validated[0].mention_type is MentionType.ROLE
    assert validated[0].valid is False
    assert "permission" in (validated[0].error or "").lower()

    # Decisive: the ungated fan-out created no notifications.
    assert _fanout(messaging, notifications, member, channel, server, content) == []

    # Non-vacuity: with the permission granted, the SAME role genuinely pings
    # every member other than the author. The gate is the only thing that
    # stopped a mass ping above -- not an inert role or broken fan-out.
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
    allowed = notifications.validate_mentions(
        member.id,
        notifications.parse_mentions(content),
        server_id=server.id,
        channel_id=channel.id,
    )
    assert allowed[0].valid is True
    notifs = _fanout(messaging, notifications, member, channel, server, content)
    assert {n.user_id for n in notifs} == {owner.id, member2.id}


def test_coverage_check_excludes_author_and_handles_tiny_servers(wired):
    """The coverage verdict is measured over the fan-out audience.

    Excluding the author is what makes the gate match the real fan-out. A
    server with no *other* members has no mass-ping audience and must not be
    classified as one (no false classification that breaks small servers).
    """
    (owner, member, member2, server, channel, servers, messaging, notifications) = wired
    db = notifications._db

    # Three-member fixture: role held by all except the author is still an
    # @everyone equivalent once the author is excluded from both counts.
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="all-others",
        permissions={},
        mentionable=True,
    )
    for uid in (owner.id, member2.id):
        servers.assign_role(owner.id, server.id, uid, role.id)
    assert role_covers_all_members(db, role.id, server.id) is False
    assert role_covers_all_members(db, role.id, server.id, member.id) is True

    # Adding the author to the role keeps the verdict true (fan-out unchanged).
    servers.assign_role(owner.id, server.id, member.id, role.id)
    assert role_covers_all_members(db, role.id, server.id, member.id) is True

    # One-member server (only the author): no other member can be pinged, so
    # it is not a mass ping even though the author holds the role.
    solo_server = servers.create_server(owner.id, f"Solo {server.id}")
    solo_role = servers.create_role(
        user_id=owner.id,
        server_id=solo_server.id,
        name="solo",
        permissions={},
        mentionable=True,
    )
    servers.assign_role(owner.id, solo_server.id, owner.id, solo_role.id)
    assert role_covers_all_members(db, solo_role.id, solo_server.id) is True
    assert role_covers_all_members(db, solo_role.id, solo_server.id, owner.id) is False
