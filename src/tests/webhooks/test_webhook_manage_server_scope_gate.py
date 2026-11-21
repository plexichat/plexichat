"""Regression: a per-channel ``webhooks.manage`` grant must not authorize
server-level webhook management writes.

``webhooks.manage`` is a SERVER-scoped permission: creating, updating,
deleting and moving webhooks and regenerating their tokens are server-level
configuration writes. The catalog groups it with the server-management
permissions (message permissions are the ones explicitly marked "per
channel"), and ``get_server_webhooks`` already checks it at server scope with
no channel id.

The webhook permission chokepoint, ``_check_manage_webhooks_permission``, used
to resolve it WITH a ``channel_id``, which applies per-channel overrides. A
member granted ``webhooks.manage`` in a single channel could therefore create,
delete and move webhooks and rotate their tokens -- a channel-scoped grant
escalated to destructive, server-scoped writes. The permission must be
resolved at server scope.
"""

import uuid

import pytest

from src.core.webhooks import PermissionDeniedError


def _channel_only_grant(servers, server, owner, member, channel):
    """Grant webhooks.manage to ``member`` in ONE channel via an override."""
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        member.id,
        allow={"webhooks.manage": True},
    )
    # Sanity: the grant is genuinely channel-scoped, not a server-wide role.
    assert not servers.get_permissions(member.id, server.id).get("webhooks.manage")
    assert servers.get_permissions(member.id, server.id, channel.id).get(
        "webhooks.manage"
    )


def _ctx(setup, servers=None):
    servers = servers or setup["servers"]
    return (
        servers,
        setup["webhooks"],
        setup["server"],
        setup["owner"],
        setup["member"],
        setup["channel"],
    )


def test_channel_scoped_manage_cannot_create_webhook(base_server_setup):
    servers, webhooks, server, owner, member, channel = _ctx(base_server_setup)
    _channel_only_grant(servers, server, owner, member, channel)

    with pytest.raises(PermissionDeniedError) as exc:
        webhooks.create_webhook(
            user_id=member.id,
            channel_id=channel.id,
            name=f"Escalated {uuid.uuid4().hex[:6]}",
        )
    assert exc.value.permission == "webhooks.manage"


def test_channel_scoped_manage_cannot_delete_webhook(base_server_setup):
    servers, webhooks, server, owner, member, channel = _ctx(base_server_setup)
    victim = webhooks.create_webhook(
        user_id=owner.id,
        channel_id=channel.id,
        name=f"Victim {uuid.uuid4().hex[:6]}",
    )
    _channel_only_grant(servers, server, owner, member, channel)

    with pytest.raises(PermissionDeniedError):
        webhooks.delete_webhook(user_id=member.id, webhook_id=victim.id)
    # The webhook must still exist after the refused deletion.
    assert webhooks.get_webhook(webhook_id=victim.id, user_id=None) is not None


def test_channel_scoped_manage_cannot_regenerate_token(base_server_setup):
    servers, webhooks, server, owner, member, channel = _ctx(base_server_setup)
    victim = webhooks.create_webhook(
        user_id=owner.id,
        channel_id=channel.id,
        name=f"Victim {uuid.uuid4().hex[:6]}",
    )
    _channel_only_grant(servers, server, owner, member, channel)

    with pytest.raises(PermissionDeniedError):
        webhooks.regenerate_token(user_id=member.id, webhook_id=victim.id)


def test_server_scoped_manage_still_can_create_webhook(base_server_setup):
    """A genuine server-wide grant keeps working (admins are not broken)."""
    servers, webhooks, server, owner, member, channel = _ctx(base_server_setup)
    role = servers.create_role(
        owner.id, server.id, "webhook-admin", permissions={"webhooks.manage": True}
    )
    servers.assign_role(owner.id, server.id, member.id, role.id)
    assert servers.get_permissions(member.id, server.id).get("webhooks.manage")

    wh = webhooks.create_webhook(
        user_id=member.id,
        channel_id=channel.id,
        name=f"Legit {uuid.uuid4().hex[:6]}",
    )
    assert wh is not None


def test_owner_still_can_manage_webhooks(base_server_setup):
    _, webhooks, _, owner, _, channel = _ctx(base_server_setup)
    wh = webhooks.create_webhook(
        user_id=owner.id,
        channel_id=channel.id,
        name=f"Owner {uuid.uuid4().hex[:6]}",
    )
    assert wh is not None
    assert webhooks.delete_webhook(user_id=owner.id, webhook_id=wh.id) is True
