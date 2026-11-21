"""Regression: moving a role must retire cached channel permission decisions.

``move_role`` changes ``srv_roles.position``, which is the tie-breaker used by
``apply_channel_overrides`` (higher position wins a deny/allow conflict) and by
the role-hierarchy helpers.  Unlike ``update_role``/``delete_role``, the move
path never invalidated the permission caches nor bumped the RBAC epoch, so a
decision computed under the old ordering survived the move.  With a deny on a
low role and an allow on a high role, moving the allow role below the deny role
left the now-invalid GRANT replayable on the very next check.
"""

import pytest

from src.core.servers.channel_access_cache import get_channel_access_cache


@pytest.fixture(autouse=True)
def _isolated_channel_access_cache():
    cache = get_channel_access_cache()
    cache.clear()
    yield
    cache.clear()


def test_move_role_revokes_stale_override_grant(server_with_channels):
    (
        server,
        owner,
        admin,
        member,
        outsider,
        general,
        announcements,
        private,
        category,
        servers,
    ) = server_with_channels

    # Two roles for the member: a low one that DENIES messages.send and a high
    # one that ALLOWs it.  While the allow role outranks the deny role, the
    # allow wins and the member can send.
    deny_role = servers.create_role(
        user_id=owner.id, server_id=server.id, name="Deny", permissions={}
    )
    allow_role = servers.create_role(
        user_id=owner.id, server_id=server.id, name="Allow", permissions={}
    )
    servers.move_role(owner.id, deny_role.id, 1)
    servers.move_role(owner.id, allow_role.id, 2)
    servers.assign_role(owner.id, server.id, member.id, deny_role.id)
    servers.assign_role(owner.id, server.id, member.id, allow_role.id)

    servers.set_channel_override(
        owner.id,
        general.id,
        "role",
        deny_role.id,
        deny={"messages.send": True},
    )
    servers.set_channel_override(
        owner.id,
        general.id,
        "role",
        allow_role.id,
        allow={"messages.send": True},
    )

    # Warm both the epoch-keyed L1 gate and the permission snapshot under the
    # current (allow-out-ranks-deny) ordering.
    assert (
        servers.has_permission(member.id, server.id, "messages.send", general.id)
        is True
    )

    # Demote the allow role below the deny role.  The deny role is now higher,
    # so its deny must win and the member must lose the permission.
    servers.move_role(owner.id, allow_role.id, 0)

    # Without invalidation this replays the stale grant; with the fix the move
    # retires the old decision and the authoritative recompute denies.
    assert (
        servers.has_permission(member.id, server.id, "messages.send", general.id)
        is False
    )
