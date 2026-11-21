"""Thread creation must honour the parent channel's view/send gates.

A server member may hold ``threads.create_public`` from a role while a channel
override denies them ``channels.view`` / ``messages.send`` on a specific
channel.  Creating a thread there must be refused just as sending a message
there is: the thread write path may not bypass the channel gate.
"""

import uuid

import pytest

from src.core.threads.exceptions import PermissionDeniedError
from src.core.threads.manager import ThreadManager


@pytest.fixture
def hidden_channel_setup(modules):
    """A server where `member` can create threads but cannot view/post in `hidden`."""
    suffix = uuid.uuid4().hex[:10]

    owner = modules.auth.register(
        username=f"owner_{suffix}",
        email=f"owner_{suffix}@example.com",
        password="TestPass123!",
    )
    member = modules.auth.register(
        username=f"member_{suffix}",
        email=f"member_{suffix}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Gate Server {suffix}")
    modules.servers.add_member(server.id, member.id)

    # Explicit grants give the member thread-creation rights at the server
    # scope, exactly like a default @everyone role.
    member_role = modules.servers.create_role(
        owner.id,
        server.id,
        "Members",
        permissions={
            "threads.create_public": True,
            "threads.create_private": True,
            "channels.view": True,
            "messages.send": True,
        },
    )
    modules.servers.assign_role(owner.id, server.id, member.id, member_role.id)

    hidden = modules.servers.create_channel(
        owner.id, server.id, "hidden", channel_type=modules.servers.ChannelType.TEXT
    )
    visible = modules.servers.create_channel(
        owner.id, server.id, "visible", channel_type=modules.servers.ChannelType.TEXT
    )

    # The owner hides `hidden` from the member by denying view and send on that
    # channel only.  Deny entries bypass the "only grant what you hold" check,
    # so `threads.create_public` stays granted from the role.
    modules.servers.set_channel_override(
        owner.id,
        hidden.id,
        "role",
        member_role.id,
        deny={"channels.view": True, "messages.send": True},
    )

    threads = ThreadManager(
        modules.threads._db,
        auth_module=modules.auth,
        messaging_module=modules.messaging,
        servers_module=modules.servers,
    )
    return owner, member, server, hidden, visible, threads, modules


def test_create_thread_denied_in_hidden_channel(hidden_channel_setup):
    _owner, member, _server, hidden, visible, threads, _modules = hidden_channel_setup

    # The gates really are closed for this member on `hidden`...
    assert not _modules.servers.has_permission(
        member.id, hidden.server_id, "channels.view", hidden.id
    )
    assert not _modules.servers.has_permission(
        member.id, hidden.server_id, "messages.send", hidden.id
    )
    # ...but the thread-create permission remains granted from the role.
    assert _modules.servers.has_permission(
        member.id, hidden.server_id, "threads.create_public", hidden.id
    )

    with pytest.raises(PermissionDeniedError):
        threads.create_thread(member.id, hidden.id, "sneaky")

    # The denial must happen before any row is written -- a thread must never
    # materialise in a channel the caller cannot view or post to.
    assert (
        _modules.threads._db.fetch_one(
            "SELECT id FROM thread_threads WHERE channel_id = ? AND deleted = 0",
            (hidden.id,),
        )
        is None
    )

    # Positive control: creation in a channel the member CAN view/post still
    # works, proving the denial is specific to the hidden channel.
    thread = threads.create_thread(member.id, visible.id, "allowed")
    assert thread.channel_id == visible.id
    assert thread.server_id == visible.server_id


def test_create_thread_from_message_denied_in_hidden_channel(hidden_channel_setup):
    owner, member, _server, hidden, _visible, threads, modules = hidden_channel_setup

    # Seed a parent message in the hidden channel as the owner.
    root = modules.messaging.send_message(
        owner.id, hidden.conversation_id, "root in hidden channel"
    )

    with pytest.raises(PermissionDeniedError):
        threads.create_thread_from_message(
            member.id,
            root.id,
            "sneaky reply thread",
            expected_channel_id=hidden.id,
        )

    assert (
        modules.threads._db.fetch_one(
            "SELECT id FROM thread_threads WHERE channel_id = ? AND deleted = 0",
            (hidden.id,),
        )
        is None
    )
