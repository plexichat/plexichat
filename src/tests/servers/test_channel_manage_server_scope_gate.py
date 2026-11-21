"""Regression: a per-channel ``channels.manage`` override must not authorize
server-level channel management.

``channels.manage`` is a server-scoped permission (create/edit/delete channels).
The channel-management handlers used to resolve it WITH a ``channel_id``, which
applies per-channel overrides. A member granted ``channels.manage`` in a single
channel could therefore delete/move/edit that channel and rewrite its overrides
-- a privilege escalation from a channel-scoped grant to a destructive,
server-scoped action. These checks must resolve the permission at server scope.
"""

import uuid

import pytest


def _scope_fixture(modules, user_pool):
    servers = modules.servers
    owner = user_pool.get_user()
    member = user_pool.get_user()
    unique = uuid.uuid4().hex[:6]
    server = servers.create_server(owner_id=owner.id, name=f"Scope {unique}")
    servers.add_member(server.id, member.id)
    channel = servers.create_channel(owner.id, server.id, f"chan-{unique}")
    return servers, server, owner, member, channel


def test_channel_scoped_manage_cannot_delete_channel(modules, user_pool):
    servers, server, owner, member, channel = _scope_fixture(modules, user_pool)

    # Owner grants the member channels.manage in ONE channel only (no role).
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        member.id,
        allow={"channels.manage": True},
    )
    # Sanity: the grant is really channel-scoped, not server-scoped.
    assert not servers.get_permissions(member.id, server.id).get("channels.manage")
    assert servers.get_permissions(member.id, server.id, channel.id).get(
        "channels.manage"
    )

    with pytest.raises(servers.PermissionDeniedError):
        servers.delete_channel(member.id, channel.id)
    # The channel must still exist after the refused deletion.
    assert servers.get_channel(channel.id, member.id) is not None


def test_channel_scoped_manage_cannot_rewrite_overrides(modules, user_pool):
    servers, server, owner, member, channel = _scope_fixture(modules, user_pool)
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        member.id,
        allow={"channels.manage": True},
    )

    with pytest.raises(servers.PermissionDeniedError):
        servers.set_channel_override(
            member.id,
            channel.id,
            "member",
            member.id,
            allow={"messages.send": True},
        )


def test_server_scoped_manage_still_can_delete_channel(modules, user_pool):
    """A genuine server-wide grant keeps working (admins are not broken)."""
    servers, server, owner, member, channel = _scope_fixture(modules, user_pool)
    role = servers.create_role(
        owner.id, server.id, "chan-mod", permissions={"channels.manage": True}
    )
    servers.assign_role(owner.id, server.id, member.id, role.id)
    assert servers.get_permissions(member.id, server.id).get("channels.manage")

    assert servers.delete_channel(member.id, channel.id) is True


def test_owner_still_can_delete_channel(modules, user_pool):
    servers, server, owner, member, channel = _scope_fixture(modules, user_pool)
    assert servers.delete_channel(owner.id, channel.id) is True
