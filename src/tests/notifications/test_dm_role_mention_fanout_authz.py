"""Regression: a role mention inside a DM must not fan out to role members
who are not part of the conversation.

A role belongs to a server; a DM has no server/channel context. Before the
fix, ``validate_mentions`` skipped the cross-server/mentionable checks for
role mentions whenever ``server_id`` was unset, so ``<@&ROLE_ID>`` in a DM
was "valid" for any role in any server. ``create_notifications`` then added
every member of that role to the fan-out, and the channel-view recipient
filter is a no-op in DMs (no ``channel_id``), so complete strangers received
a notification carrying the DM's content preview.
"""

from src.core.notifications.models import MentionType


def test_dm_role_mention_does_not_notify_unrelated_role_members(modules, user_pool):
    attacker = user_pool.get_user()
    dm_peer = user_pool.get_user()
    victim = user_pool.get_user()

    # A role in an unrelated server, with ``victim`` as its only member.
    server = modules.servers.create_server(attacker.id, "Unrelated server")
    modules.servers.add_member(server.id, victim.id)
    role = modules.servers.create_role(
        user_id=attacker.id,
        server_id=server.id,
        name="vip",
        permissions={},
        mentionable=True,
    )
    modules.servers.assign_role(attacker.id, server.id, victim.id, role.id)

    # Attacker DMs only ``dm_peer``; ``victim`` is not a participant.
    dm = modules.messaging.create_dm(attacker.id, dm_peer.id)
    content = f"hey <@&{role.id}> look at this secret"
    msg = modules.messaging.send_message(attacker.id, dm.id, content)

    notifs = modules.notifications.create_notifications_for_message(
        message_id=msg.id,
        author_id=attacker.id,
        conversation_id=dm.id,
        content=content,
        server_id=None,
        channel_id=None,
    )

    victim_notifs = [n for n in notifs if n.user_id == victim.id]
    assert victim_notifs == [], (
        "role mention in a DM leaked a notification to an unrelated "
        "role member who is not part of the conversation"
    )


def test_dm_role_mention_is_marked_invalid(modules, user_pool):
    """The mention itself must be rejected in DM context (fail closed)."""
    attacker = user_pool.get_user()
    server = modules.servers.create_server(attacker.id, "Role home")
    role = modules.servers.create_role(
        user_id=attacker.id,
        server_id=server.id,
        name="vip",
        permissions={},
        mentionable=True,
    )

    mentions = modules.notifications.parse_mentions(f"<@&{role.id}>")
    validated = modules.notifications.validate_mentions(
        attacker.id, mentions, server_id=None, channel_id=None
    )

    role_mentions = [m for m in validated if m.mention_type == MentionType.ROLE]
    assert role_mentions
    assert all(not m.valid for m in role_mentions), (
        "role mention must be invalid in a DM (no server context)"
    )
