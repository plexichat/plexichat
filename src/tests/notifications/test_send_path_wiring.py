"""Regression tests: the production message-send path must create
server-side mention notifications, and mention fan-out must exclude users
who lack channel view permission (content_preview leak prevention).
Covers wiring that direct creator-call tests cannot exercise.
"""

import uuid

import pytest


@pytest.fixture
def wired_notifications(modules):
    """Register the module-level notifications API against this test's db.

    The ``modules`` fixture wires a bare NotificationManager; the real
    server initializer also registers the module-level API (which is what
    the send-path hook consults). The previous global state is restored on
    teardown so other suites are never affected.
    """
    from src.core import notifications as nm

    saved_manager = getattr(nm, "_manager", None)
    saved_complete = getattr(nm, "_setup_complete", False)
    if not saved_complete:
        nm.setup(
            modules._db,
            modules.auth,
            modules.messaging,
            modules.servers,
            modules.relationships,
            modules.presence,
        )
    yield nm
    nm._manager = saved_manager
    nm._setup_complete = saved_complete


def _mkuser(modules, name):
    return modules.auth.register(
        username=f"{name}_{uuid.uuid4().hex[:8]}",
        email=f"{name}_{uuid.uuid4().hex[:6]}@example.com",
        password="TestPass123!",
    )


def _make_server_channel(modules, owner, name):
    server = modules.servers.create_server(owner.id, f"Wiring Server {name}")
    channel = modules.servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name=name,
        channel_type=modules.servers.ChannelType.TEXT,
    )
    conv_id = modules.servers.get_channel_conversation_id(channel.id)
    return server, channel, conv_id


def test_send_message_creates_server_notification(modules, wired_notifications):
    owner = _mkuser(modules, "wireown")
    member = _mkuser(modules, "wiremem")

    server, channel, conv_id = _make_server_channel(modules, owner, "general")
    modules.servers.add_member(server.id, member.id)

    msg = modules.messaging.send_message(owner.id, conv_id, f"hi <@{member.id}>")
    assert msg is not None

    notifs = [
        n
        for n in wired_notifications.get_notifications(member.id, limit=50)
        if n.message_id == msg.id
    ]
    assert notifs, "message-send path must create server-side mention notifications"
    assert notifs[0].mention_type.value == "user"
    assert notifs[0].channel_id == channel.id


def test_mention_fanout_filters_non_viewers(modules, wired_notifications):
    owner = _mkuser(modules, "wireown2")
    insider = _mkuser(modules, "wirein")
    outsider = _mkuser(modules, "wireout")

    server, channel, conv_id = _make_server_channel(modules, owner, "secret")
    for uid in (insider.id, outsider.id):
        modules.servers.add_member(server.id, uid)

    # Deny the outsider channel view via a member override.
    modules.servers.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=outsider.id,
        deny={"channels.view": True},
    )
    assert not modules.servers.has_permission(
        outsider.id, server.id, "channels.view", channel.id
    ), "override must deny view before the send"

    msg = modules.messaging.send_message(
        owner.id, conv_id, f"psst <@{insider.id}> <@{outsider.id}>"
    )
    assert msg is not None

    insider_ids = {
        n.user_id
        for n in wired_notifications.get_notifications(insider.id)
        if n.message_id == msg.id
    }
    outsider_ids = {
        n.user_id
        for n in wired_notifications.get_notifications(outsider.id)
        if n.message_id == msg.id
    }
    assert insider.id in insider_ids, "viewer may be notified"
    assert outsider.id not in outsider_ids, (
        "non-viewer must NOT receive notification / content preview"
    )


def test_dm_message_no_notification_crash(modules, wired_notifications):
    u1 = _mkuser(modules, "wiredm1")
    u2 = _mkuser(modules, "wiredm2")
    dm = modules.messaging.create_dm(u1.id, u2.id)
    msg = modules.messaging.send_message(u1.id, dm.id, f"yo <@{u2.id}>")
    assert msg is not None
