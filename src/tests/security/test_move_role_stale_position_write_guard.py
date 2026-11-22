"""A role move must re-assert the hierarchy at write time.

``update_role`` and ``delete_role`` both guard their write with a CAS on the
position their hierarchy decision was made against (``WHERE id = ? AND
position = ?``).  ``move_role`` makes the same read-then-decide-then-write
sequence -- it checks ``can_manage_role`` against ``role.position`` and checks
the destination is below the actor's highest role -- but its UPDATE is keyed
only by ``id``.  A concurrent promotion of the target role between the check
and the write therefore lets a stale, previously-authorized decision move a
role that is now at/above the actor's own rank.

This regression injects exactly that promotion in the gap and requires the
move to fail closed, mirroring ``test_role_delete_stale_position_write_guard``.
"""

import pytest

from src.core.servers.exceptions import RoleHierarchyError


def test_move_role_rejects_role_promoted_between_check_and_write(
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
        if not promoted["done"] and upper.startswith(
            "UPDATE SRV_ROLES SET POSITION = ?"
        ):
            # Promote the role *after* the hierarchy check has passed but
            # before the move lands, exactly as a concurrent owner would.
            promoted["done"] = True
            original_execute(
                "UPDATE srv_roles SET position = 20 WHERE id = ?", (target.id,)
            )
        return original_execute(sql, params)

    monkeypatch.setattr(db, "execute", execute)

    with pytest.raises(RoleHierarchyError):
        servers.move_role(moderator.id, target.id, 6)

    assert promoted["done"]
    row = db.fetch_one("SELECT position FROM srv_roles WHERE id = ?", (target.id,))
    assert row is not None
    assert row["position"] == 20
