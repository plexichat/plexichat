"""Role assignment must re-validate grantability against the role's current bits.

``assign_role`` checked the role's permission payload *before* taking the
server's advisory locks, then attached the role by id inside the lock. A
concurrent privileged edit (``update_role`` takes the same 142/143 locks) could
widen that payload in the gap, so the moderator's stale "you may grant these
bits" decision ended up handing the target a role carrying permissions the
moderator never held -- including ``administrator``.

The fix re-reads the authoritative payload under the lock and re-runs both
validation and the grantability gate before the insert. This test lands the
escalation in the gap deterministically.
"""

import json

import pytest

from src.core.servers.exceptions import PermissionDeniedError


def test_assign_role_denies_when_role_permissions_escalate_in_gap(
    server_with_members, monkeypatch
):
    server, owner, _admin, member, _outsider, _admin_role, servers = (
        server_with_members
    )

    # A low-ranked moderator may hand out roles but must not be able to grant
    # permissions it does not itself possess -- especially administrator.
    mod_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Grantability Moderator",
        permissions={"members.manage_roles": True},
    )
    servers.move_role(owner.id, mod_role.id, 5)
    servers.assign_role(owner.id, server.id, member.id, mod_role.id)

    # The role is safe at check time: it only carries a bit the moderator holds
    # via @everyone.
    target_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Benign Role",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, target_role.id, 2)

    real_fetch_one = servers._db.fetch_one
    real_execute = servers._db.execute
    state = {"escalated": False}

    def racing_fetch_one(sql, params=()):
        if "SELECT 1 FROM srv_member_roles" in sql and not state["escalated"]:
            # This lookup sits immediately after the pre-lock grantability
            # check and before the write. Land a concurrent privileged edit of
            # the SAME role here, exactly in the check->write gap.
            state["escalated"] = True
            real_execute(
                "UPDATE srv_roles SET permissions = ? WHERE id = ?",
                (
                    json.dumps({"messages.send": True, "administrator": True}),
                    target_role.id,
                ),
            )
            servers._invalidate_server_permission_caches(server.id)
        return real_fetch_one(sql, params)

    monkeypatch.setattr(servers._db, "fetch_one", racing_fetch_one)

    with pytest.raises(PermissionDeniedError):
        servers.assign_role(member.id, server.id, member.id, target_role.id)

    # Non-vacuous: the escalation really landed and the role really was one
    # the moderator could have attached under the old snapshot-only check.
    assert state["escalated"] is True
    row = servers._db.fetch_one(
        "SELECT permissions FROM srv_roles WHERE id = ?", (target_role.id,)
    )
    assert json.loads(row["permissions"]).get("administrator") is True
    assigned = [r.id for r in servers.get_member_roles(server.id, member.id)]
    assert target_role.id not in assigned
    assert servers.has_permission(member.id, server.id, "administrator") is False


def test_assign_role_still_succeeds_without_escalation(server_with_members):
    """The re-check must not refuse a genuinely grantable assignment."""
    server, owner, _admin, member, _outsider, _admin_role, servers = (
        server_with_members
    )

    mod_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Grantability Moderator",
        permissions={"members.manage_roles": True},
    )
    servers.move_role(owner.id, mod_role.id, 5)
    servers.assign_role(owner.id, server.id, member.id, mod_role.id)

    target_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Still Benign",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, target_role.id, 2)

    assert servers.assign_role(member.id, server.id, member.id, target_role.id) is True
    assert target_role.id in [
        r.id for r in servers.get_member_roles(server.id, member.id)
    ]
