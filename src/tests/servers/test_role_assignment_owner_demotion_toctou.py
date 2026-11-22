"""assign_role must re-derive ownership under the 142/143 locks, not reuse a
pre-lock verdict.

``transfer_ownership`` is CAS-guarded but deliberately does NOT take the
142 -> 143 advisory locks that ``assign_role`` holds across its under-lock
re-check + INSERT. So an actor who is the owner at the pre-lock hierarchy
check can lose ownership in the check->write gap. The rank re-check added for
N840 re-reads the role's position and the actor's role rows, but it still uses
the ``is_owner`` boolean captured *before* the locks. A stale ``is_owner=True``
makes ``can_manage_role`` return True unconditionally, so a now-former owner
can attach a role sitting at/above their (now lower) rank -- the exact
escalation the rank gate exists to prevent.

This test lands the ownership transfer deterministically between the pre-lock
check and the under-lock write. It passes only when assign_role re-derives
ownership from the authoritative row under the lock and fails closed.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_assign_role_denies_when_actor_loses_ownership_in_gap(
    server_with_members, monkeypatch
):
    server, owner, _admin, member, outsider, _admin_role, servers = server_with_members

    # Give the owner a real role so we can place their rank between the target
    # member's rank and the target role's rank. That way the member-target
    # guard keeps passing after demotion and the ONLY thing authorizing the
    # grant is the stale owner verdict.
    staff_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Owner Staff",
        permissions={"messages.send": True},
    )
    servers.assign_role(owner.id, server.id, owner.id, staff_role.id)

    member_max = max(
        (
            int(r["position"])
            for r in servers._get_member_role_rows(server.id, member.id)
        ),
        default=0,
    )
    staff_pos = member_max + 5
    target_pos = staff_pos + 10
    servers.move_role(owner.id, staff_role.id, staff_pos)

    target_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Above Rank Role",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, target_role.id, target_pos)

    owner_max_after = max(
        int(r["position"]) for r in servers._get_member_role_rows(server.id, owner.id)
    )
    assert staff_pos > member_max  # member-target guard keeps passing
    assert owner_max_after < target_pos  # non-owner would be denied by rank
    assert outsider.id not in (owner.id, member.id)

    real_fetch_one = servers._db.fetch_one
    real_execute = servers._db.execute
    state = {"demoted": False}

    def racing_fetch_one(sql, params=None):
        if (
            not state["demoted"]
            and isinstance(sql, str)
            and sql.startswith("SELECT permissions FROM srv_roles")
            and params == (target_role.id,)
        ):
            # transfer_ownership does not take the 142/143 locks this path
            # holds. Land it in the check->write gap: the former owner is now a
            # plain member whose rank sits below the role being attached.
            real_execute(
                "UPDATE srv_servers SET owner_id = ? WHERE id = ?",
                (outsider.id, server.id),
            )
            servers._invalidate_owner_cache(server.id)
            state["demoted"] = True
        return real_fetch_one(sql, params)

    monkeypatch.setattr(servers._db, "fetch_one", racing_fetch_one)

    with pytest.raises(RoleHierarchyError):
        servers.assign_role(owner.id, server.id, member.id, target_role.id)

    # Non-vacuous: the ownership change really landed, and the escalated role
    # really was not attached to the grantee.
    assert state["demoted"] is True
    assert [r.id for r in servers.get_member_roles(server.id, member.id)].count(
        target_role.id
    ) == 0
