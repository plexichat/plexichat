"""Regression: a non-owner must not be able to rewrite @everyone permissions.

``update_role`` refuses renaming/moving/deleting the default role, but it let
any holder of ``roles.manage`` add permissions to @everyone.  Because
``check_permissions_grantable`` only requires that the editor *possesses* the
permission, a moderator could grant ``roles.manage`` (or any other bit they
hold) to @everyone and hand that capability to every member of the server.

The owner may still edit @everyone (the long-standing, intended behaviour).
"""

import pytest


def test_moderator_cannot_grant_permissions_to_everyone(server_with_members):
    server, owner, admin_user, member_user, outsider, admin_role, servers = (
        server_with_members
    )

    # Give a non-owner member the roles.manage capability and a real role row so
    # they clear both the capability and the rank gate for @everyone (pos 0).
    mod_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="RoleMod",
        permissions={"roles.manage": True},
    )
    servers.assign_role(owner.id, server.id, member_user.id, mod_role.id)

    default_role = [
        r for r in servers.get_roles(owner.id, server.id) if r.is_default
    ][0]

    # Before the fix this succeeded, so @everyone gained roles.manage and every
    # member with it -- a privilege escalation for the whole server.
    with pytest.raises(servers.DefaultRoleError):
        servers.update_role(
            member_user.id,
            default_role.id,
            permissions={"roles.manage": True},
        )

    after = servers.get_role(default_role.id, owner.id)
    assert not after.permissions.get("roles.manage")


def test_owner_can_still_grant_permissions_to_everyone(server_with_members):
    """The fix must not break the legitimate owner-only @everyone edit."""
    server, owner, admin_user, member_user, outsider, admin_role, servers = (
        server_with_members
    )

    default_role = [
        r for r in servers.get_roles(owner.id, server.id) if r.is_default
    ][0]

    updated = servers.update_role(
        owner.id,
        default_role.id,
        permissions={"messages.send": False},
    )
    assert updated.permissions.get("messages.send") is False
