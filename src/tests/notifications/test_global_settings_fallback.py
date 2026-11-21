"""Regression: a user's GLOBAL mention-suppression settings must be honored
by the server-channel notification fan-out.

Settings exist at two levels: global and per-server, and a per-server row
overrides the global one. ``create_notifications`` resolves the recipients'
settings with ``get_notification_settings_bulk(user_ids, server_id)``. Before
the fix that lookup only selected rows with ``server_id = ?`` and fabricated a
fresh default (``suppress_everyone=False``, ``suppress_roles=False``,
``level=ALL_MESSAGES``) for users without a per-server row. A user who had
globally opted out of @everyone / role pings therefore still received those
notifications -- including the content preview -- in every server where they
had not saved a server-specific setting.
"""

import uuid

import pytest

from src.core.notifications.models import NotificationLevel


@pytest.fixture
def wired_notifications(modules):
    """Attach the server/presence dependencies the fan-out gates rely on."""
    mgr = modules.notifications
    mgr._servers = modules.servers
    mgr._presence = modules.presence
    mgr._relationships = modules.relationships
    return mgr


def _user(modules, name):
    return modules.auth.register(
        username=f"{name}_{uuid.uuid4().hex[:8]}",
        email=f"{name}_{uuid.uuid4().hex[:6]}@example.com",
        password="TestPass123!",
    )


def _server_channel(modules, owner, name):
    server = modules.servers.create_server(owner.id, f"Settings Server {name}")
    channel = modules.servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name=name,
        channel_type=modules.servers.ChannelType.TEXT,
    )
    conv_id = modules.servers.get_channel_conversation_id(channel.id)
    return server, channel, conv_id


def _fanout(modules, author, conv_id, server, channel, content):
    msg = modules.messaging.send_message(author.id, conv_id, content)
    return modules.notifications.create_notifications_for_message(
        message_id=msg.id,
        author_id=author.id,
        conversation_id=conv_id,
        content=content,
        server_id=server.id,
        channel_id=channel.id,
    )


def test_global_suppress_everyone_honored_in_server_fanout(
    modules, wired_notifications
):
    owner = _user(modules, "setown")
    muted = _user(modules, "setmem")
    control = _user(modules, "setctl")
    server, channel, conv_id = _server_channel(modules, owner, "general")
    for uid in (muted.id, control.id):
        modules.servers.add_member(server.id, uid)

    # The muted member globally opts out of @everyone/@here pings and has NO
    # per-server notification settings row. The control has no settings at all.
    modules.notifications.update_notification_settings(muted.id, suppress_everyone=True)

    notifs = _fanout(modules, owner, conv_id, server, channel, "@everyone hello")
    notified = {n.user_id for n in notifs}
    assert control.id in notified, "control recipient proves the fan-out ran"
    assert muted.id not in notified, (
        "global suppress_everyone must suppress @everyone in a server where "
        "the user has no per-server override"
    )


def test_global_suppress_roles_honored_in_server_fanout(modules, wired_notifications):
    owner = _user(modules, "setown2")
    muted = _user(modules, "setmem2")
    control = _user(modules, "setctl2")
    server, channel, conv_id = _server_channel(modules, owner, "roles")
    for uid in (muted.id, control.id):
        modules.servers.add_member(server.id, uid)
    role = modules.servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="crew",
        permissions={},
        mentionable=True,
    )
    modules.servers.assign_role(owner.id, server.id, muted.id, role.id)
    modules.servers.assign_role(owner.id, server.id, control.id, role.id)

    modules.notifications.update_notification_settings(muted.id, suppress_roles=True)

    notifs = _fanout(modules, owner, conv_id, server, channel, f"ping <@&{role.id}>")
    notified = {n.user_id for n in notifs}
    assert control.id in notified, "control recipient proves the fan-out ran"
    assert muted.id not in notified, (
        "global suppress_roles must suppress role mentions in a server where "
        "the user has no per-server override"
    )


def test_global_nothing_level_honored_in_server_fanout(modules, wired_notifications):
    owner = _user(modules, "setown3")
    muted = _user(modules, "setmem3")
    control = _user(modules, "setctl3")
    server, channel, conv_id = _server_channel(modules, owner, "muted")
    for uid in (muted.id, control.id):
        modules.servers.add_member(server.id, uid)

    modules.notifications.update_notification_settings(
        muted.id, level=NotificationLevel.NOTHING
    )

    notifs = _fanout(
        modules,
        owner,
        conv_id,
        server,
        channel,
        f"hi <@{muted.id}> <@{control.id}>",
    )
    notified = {n.user_id for n in notifs}
    assert control.id in notified, "control recipient proves the fan-out ran"
    assert muted.id not in notified, (
        "global NOTHING level must suppress server mentions"
    )


def test_per_server_setting_overrides_global(modules, wired_notifications):
    """A per-server row stays authoritative -- the fallback must not merge it."""
    owner = _user(modules, "setown4")
    member = _user(modules, "setmem4")
    server, channel, conv_id = _server_channel(modules, owner, "override")
    modules.servers.add_member(server.id, member.id)

    # Globally muted, but explicitly opted IN to @everyone for this server.
    modules.notifications.update_notification_settings(
        member.id, level=NotificationLevel.NOTHING, suppress_everyone=True
    )
    modules.notifications.update_notification_settings(
        member.id,
        server_id=server.id,
        level=NotificationLevel.ALL_MESSAGES,
        suppress_everyone=False,
    )

    notifs = _fanout(modules, owner, conv_id, server, channel, "@everyone hello")
    assert member.id in {n.user_id for n in notifs}, (
        "an explicit per-server setting must override the global one"
    )
