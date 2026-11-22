"""A role deletion must re-assert the hierarchy at write time.

``delete_role`` reads the target role's position, checks it is below the
actor's highest role, and then issues an unguarded
``UPDATE srv_roles SET deleted = 1 WHERE id = ?``.  That is the same
read-then-write window ``update_role`` closes with a CAS predicate: a
concurrent promotion of the role between the check and the write lets a
stale, previously-authorized decision delete a role that has just moved to
at/above the actor's own rank.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_delete_role_rejects_role_promoted_between_check_and_write(
    fresh_server, monkeypatch
):
    server = fresh_server["server"]
    owner = fresh_server["owner"]
    moderator = fresh_server["member"]
    servers = fresh_server["servers"]

    mod_role = servers.create_role(
        owner.id, server.id, "mod", permissions={"roles.manage": True}
    )
    servers.move_role(owner.id, mod_role.id, 10)
    servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)

    target = servers.create_role(owner.id, server.id, "target", permissions={})
    original_position = 5
    servers.move_role(owner.id, target.id, original_position)

    db = servers.role_handler.db
    original_execute = db.execute
    promoted = {"done": False}

    def execute(sql, params=()):
        upper = " ".join(sql.upper().split())
        if not promoted["done"] and upper.startswith(
            "UPDATE SRV_ROLES SET DELETED = 1"
        ):
            # Promote the role *after* the hierarchy check has passed but
            # before the deletion lands, exactly as a concurrent owner would.
            promoted["done"] = True
            original_execute(
                "UPDATE srv_roles SET position = 20 WHERE id = ?", (target.id,)
            )
        return original_execute(sql, params)

    monkeypatch.setattr(db, "execute", execute)

    with pytest.raises(RoleHierarchyError):
        servers.delete_role(moderator.id, target.id)

    assert promoted["done"]
    row = db.fetch_one(
        "SELECT position, deleted FROM srv_roles WHERE id = ?", (target.id,)
    )
    assert row is not None
    # The CAS guard rejects the stale write with RoleHierarchyError, and
    # delete_role's except block rolls the transaction back. That rollback
    # also undoes the injected promotion, so the role ends up back at its
    # original position -- not at 20 -- and must still be present.
    assert row["position"] == original_position
    assert row["deleted"] == 0
