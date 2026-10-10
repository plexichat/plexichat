"""Temporary-invite membership enforcement (Agent 16 F9).

The ``temporary`` invite flag used to be stored (and echoed back) but never
acted on, so a "temporary" invite granted permanent membership. A
temporary membership is now revoked when the member's last connection
closes, unless the member earned a role in the meantime.
"""

from __future__ import annotations

import uuid


def _temp_invite(servers, owner, channel):
    return servers.create_invite(
        user_id=owner.id, channel_id=channel.id, temporary=True
    )


def test_temporary_membership_is_revoked_on_disconnect(server_with_channels):
    server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels

    invite = _temp_invite(servers, owner, general)
    member = servers.use_invite(outsider.id, invite.code)
    assert member.is_temporary is True, "join via a temporary invite must be flagged"
    assert servers.get_member(server.id, outsider.id) is not None

    revoked = servers.purge_temporary_members(outsider.id)

    assert server.id in revoked, "the temporary membership's server must be reported"
    assert servers.get_member(server.id, outsider.id) is None, (
        "a temporary member with no roles must be removed on last disconnect"
    )


def test_temporary_member_kept_when_granted_a_role(server_with_channels):
    server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels

    invite = _temp_invite(servers, owner, general)
    servers.use_invite(outsider.id, invite.code)

    # A non-default role makes the membership permanent.
    role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name=f"Temp {uuid.uuid4().hex[:6]}",
        permissions={"channels.view": True},
    )
    servers.assign_role(owner.id, server.id, outsider.id, role.id)

    revoked = servers.purge_temporary_members(outsider.id)

    assert revoked == [], "a member who earned a role must not be revoked"
    kept = servers.get_member(server.id, outsider.id)
    assert kept is not None
    assert kept.is_temporary is False, "the temporary flag must be cleared"


def test_permanent_membership_is_never_purged(server_with_channels):
    server, owner, _, member_user, _, _, _, _, _, servers = server_with_channels

    # member_user joined normally (not via a temporary invite).
    assert servers.get_member(server.id, member_user.id).is_temporary is False
    assert servers.purge_temporary_members(member_user.id) == []
    assert servers.get_member(server.id, member_user.id) is not None


def test_non_temporary_invite_join_stays_permanent(server_with_channels):
    server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels

    invite = servers.create_invite(
        user_id=owner.id, channel_id=general.id, temporary=False
    )
    member = servers.use_invite(outsider.id, invite.code)
    assert member.is_temporary is False

    assert servers.purge_temporary_members(outsider.id) == []
    assert servers.get_member(server.id, outsider.id) is not None
