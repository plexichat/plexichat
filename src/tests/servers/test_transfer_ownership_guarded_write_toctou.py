"""Stale-state probes: ``transfer_ownership`` must guard the write, not just
the read.

``transfer_ownership`` checks (a) the caller still owns the server and (b) the
target is still a member, then updates ``srv_servers.owner_id`` by id only.
Both conditions can change in the window before the write.  The mutating
statement must re-check them in its WHERE clause and fail closed on a lost
race (same guarded-write pattern as ``delete_server``/``approve_request``).
"""

import pytest

from src.core.servers.exceptions import ServerAccessDeniedError


def _wrap_execute(manager, trigger_prefix, inject):
    """Intercept a mutating statement and land a concurrent change first."""
    real_execute = manager._db.execute

    def wrapped(query, params=None, *args, **kwargs):
        if isinstance(query, str) and query.lstrip().upper().startswith(trigger_prefix):
            inject(real_execute)
        return real_execute(query, params, *args, **kwargs)

    manager._db.execute = wrapped
    return real_execute


def test_transfer_denied_when_owner_changes_before_write(server_manager, three_users):
    owner, new_owner, third = three_users
    server = server_manager.create_server(owner.id, "TOCTOU transfer server")
    server_manager.add_member(server.id, new_owner.id)
    server_manager.add_member(server.id, third.id)

    def inject(real_execute):
        # Concurrent reassignment lands after the owner check, before the write.
        real_execute(
            "UPDATE srv_servers SET owner_id = ? WHERE id = ?",
            (third.id, server.id),
        )

    real_execute = _wrap_execute(
        server_manager, "UPDATE SRV_SERVERS SET OWNER_ID = ?", inject
    )
    try:
        with pytest.raises(ServerAccessDeniedError):
            server_manager.transfer_ownership(owner.id, server.id, new_owner.id)
    finally:
        server_manager._db.execute = real_execute

    row = server_manager._db.fetch_one(
        "SELECT owner_id FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert int(row["owner_id"]) == int(third.id)


def test_transfer_denied_when_target_removed_before_write(server_manager, two_users):
    owner, new_owner = two_users
    server = server_manager.create_server(owner.id, "TOCTOU target server")
    server_manager.add_member(server.id, new_owner.id)

    def inject(real_execute):
        # Target is kicked after the membership check, before the write. Use
        # the manager's own removal path so member-role rows cascade cleanly.
        server_manager.kick_member(owner.id, server.id, new_owner.id)

    real_execute = _wrap_execute(
        server_manager, "UPDATE SRV_SERVERS SET OWNER_ID = ?", inject
    )
    try:
        with pytest.raises(ServerAccessDeniedError):
            server_manager.transfer_ownership(owner.id, server.id, new_owner.id)
    finally:
        server_manager._db.execute = real_execute

    row = server_manager._db.fetch_one(
        "SELECT owner_id FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert int(row["owner_id"]) == int(owner.id)
    assert not server_manager._is_member(server.id, new_owner.id)


def test_legitimate_transfer_still_succeeds(server_manager, two_users):
    """Guard against over-hardening: an uncontested transfer still works."""
    owner, new_owner = two_users
    server = server_manager.create_server(owner.id, "Normal transfer server")
    server_manager.add_member(server.id, new_owner.id)

    updated = server_manager.transfer_ownership(owner.id, server.id, new_owner.id)
    assert int(updated.owner_id) == int(new_owner.id)
    row = server_manager._db.fetch_one(
        "SELECT owner_id FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert int(row["owner_id"]) == int(new_owner.id)
