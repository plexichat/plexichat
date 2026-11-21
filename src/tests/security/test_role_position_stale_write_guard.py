"""A role mutation must re-assert the hierarchy at write time.

``update_role`` reads the target role's position, checks it is below the
actor's highest role, and then writes in a separate UPDATE with no guard on
the position it just authorized. A concurrent promotion of that role between
the read and the write lets a stale, previously-authorized decision edit a
role that is now at/above the actor's own rank.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_update_role_rejects_role_promoted_between_check_and_write(
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
    servers.move_role(owner.id, target.id, 5)

    db = servers.role_handler.db
    original_execute = db.execute
    promoted = {"done": False}

    def execute(sql, params=()):
        upper = " ".join(sql.upper().split())
        if (
            not promoted["done"]
            and upper.startswith("UPDATE SRV_ROLES SET")
            and "PERMISSIONS = ?" in upper
        ):
            promoted["done"] = True
            original_execute(
                "UPDATE srv_roles SET position = 20 WHERE id = ?", (target.id,)
            )
        return original_execute(sql, params)

    monkeypatch.setattr(db, "execute", execute)

    with pytest.raises(RoleHierarchyError):
        servers.update_role(
            moderator.id, target.id, permissions={"messages.send": True}
        )
    assert promoted["done"]
