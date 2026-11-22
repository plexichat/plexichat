"""assign_role must re-check rank against the role's live position at write time.

``assign_role`` validates ``can_manage_role`` (and ``_guard_member_target``)
*before* taking the server's 142/143 advisory locks. Under those locks it only
re-reads the role's permission payload; the role's position is never re-read.
``move_role`` is now CAS-guarded but deliberately does not take the 142/143
locks, so an owner can promote that same role into the gap. The stale
"you outrank this role" decision then attaches a role whose live position is
at/above the editor's own rank, elevating the grantee over the editor -- the
exact escalation ``can_manage_role`` exists to prevent.

This test lands the promotion deterministically and fails closed only when the
grant re-checks the authoritative position before the insert.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_assign_role_denies_when_role_position_rises_in_gap(
    server_with_members, monkeypatch
):
    server, owner, admin, member, _outsider, _admin_role, servers = (
        server_with_members
    )

    # The actor holds ``members.manage_roles`` via its assigned role and can
    # initially outrank the target role; the grantee sits below both.
    target_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Rank Race Role",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, target_role.id, 0)

    actor_max = max(
        int(r["position"])
        for r in servers._get_member_role_rows(server.id, admin.id)
    )
    assert actor_max > 0
    assert actor_max > 0  # the pre-lock rank check must initially pass

    real_fetch_one = servers._db.fetch_one
    real_execute = servers._db.execute
    state = {"escalated_to": None}

    def racing_fetch_one(sql, params=None):
        if (
            state["escalated_to"] is None
            and isinstance(sql, str)
            and sql.startswith("SELECT permissions FROM srv_roles")
            and params == (target_role.id,)
        ):
            # A concurrent owner promotion lands after the pre-lock rank check
            # but before the under-lock write. move_role is CAS-guarded on its
            # own, but it never takes the 142/143 locks this path holds.
            real_execute(
                "UPDATE srv_roles SET position = ? WHERE id = ?",
                (50, target_role.id),
            )
            servers._invalidate_server_permission_caches(server.id)
            state["escalated_to"] = 50
        return real_fetch_one(sql, params)

    monkeypatch.setattr(servers._db, "fetch_one", racing_fetch_one)

    with pytest.raises(RoleHierarchyError):
        servers.assign_role(admin.id, server.id, member.id, target_role.id)

    # Non-vacuous: the race really promoted the role far above the actor, and
    # the escalated role really was not attached to the grantee.
    assert state["escalated_to"] == 50
    assert actor_max < state["escalated_to"]
    assigned = [r.id for r in servers.get_member_roles(server.id, member.id)]
    assert target_role.id not in assigned
