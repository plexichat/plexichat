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

This test makes the authoritative owner read at write time report a different
owner, deterministically landing the ownership loss in the check->write gap.
It passes only when assign_role re-derives ownership from that row under the
lock and fails closed. Without the fix that owner query is never issued, so
the grant succeeds and the test fails -- proving non-vacuity.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_assign_role_denies_when_actor_loses_ownership_in_gap(
    server_with_members, monkeypatch
):
    server, owner, _admin, member, outsider, _admin_role, servers = server_with_members

    # The owner has only the default role (position 0); the target role sits
    # above it. Pre-lock the owner verdict bypasses the rank gate; at write
    # time a non-owner must be denied by it. Empty permissions keep the
    # grantability re-check out of the way so the rank gate is the sole decider.
    target_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Above Rank Role",
        permissions={},
    )
    assert target_role.position > 0
    owner_max = max(
        int(r["position"]) for r in servers._get_member_role_rows(server.id, owner.id)
    )
    assert owner_max < target_role.position

    real_fetch_one = servers._db.fetch_one
    state = {"race": False, "owner_overrides": 0}

    def racing_fetch_one(sql, params=None):
        # The under-lock permission re-read is the exact point a concurrent
        # transfer_ownership can land (it takes no 142/143 lock).
        if (
            not state["race"]
            and isinstance(sql, str)
            and sql.startswith("SELECT permissions FROM srv_roles")
            and params == (target_role.id,)
        ):
            state["race"] = True
        # From that point on, the authoritative owner row says the actor is no
        # longer the owner. Without the fix this query is never issued here.
        if (
            state["race"]
            and isinstance(sql, str)
            and sql.startswith("SELECT owner_id FROM srv_servers")
            and params == (server.id,)
        ):
            state["owner_overrides"] += 1
            return {"owner_id": outsider.id}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(servers._db, "fetch_one", racing_fetch_one)

    with pytest.raises(RoleHierarchyError):
        servers.assign_role(owner.id, server.id, member.id, target_role.id)

    # Non-vacuity: the gap was reached, the write-time owner read was observed,
    # and the escalated role was not attached to the grantee.
    assert state["race"] is True
    assert state["owner_overrides"] >= 1
    assert target_role.id not in [
        r.id for r in servers.get_member_roles(server.id, member.id)
    ]
