"""Stale-state probe: ``delete_server`` re-checks ownership at delete time.

``delete_server`` loads a server, checks ``server.owner_id == user_id``, and
then soft-deletes the row by id only.  If ownership transfers in the window
between that check and the ``UPDATE``, the former owner would still delete a
server that no longer belongs to them.  The mutating statement must therefore
carry the authoritative ownership predicate itself, and fail closed when it
matches no row (the same guarded-write pattern used by ``approve_request``).
"""

import pytest

from src.core.servers.exceptions import ServerAccessDeniedError


def test_delete_server_denied_when_owner_changes_between_check_and_delete(
    server_manager, two_users
):
    owner, new_owner = two_users
    server = server_manager.create_server(owner.id, "TOCTOU server")
    server_manager.add_member(server.id, new_owner.id)

    real_execute = server_manager._db.execute

    def racing_execute(query, params=None, *args, **kwargs):
        # Land a concurrent ownership transfer in the gap between the owner
        # check and the soft-delete statement.  The caller already passed the
        # check against the stale (owner-owned) snapshot.
        if isinstance(query, str) and query.lstrip().upper().startswith(
            "UPDATE SRV_SERVERS SET DELETED = 1"
        ):
            real_execute(
                "UPDATE srv_servers SET owner_id = ? WHERE id = ?",
                (new_owner.id, server.id),
            )
        return real_execute(query, params, *args, **kwargs)

    server_manager._db.execute = racing_execute
    try:
        with pytest.raises(ServerAccessDeniedError):
            server_manager.delete_server(owner.id, server.id)
    finally:
        server_manager._db.execute = real_execute

    # The server must survive: it now belongs to new_owner, who never
    # authorized this delete.
    row = server_manager._db.fetch_one(
        "SELECT deleted, owner_id FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert row is not None
    assert int(row["deleted"]) == 0
    assert int(row["owner_id"]) == int(new_owner.id)


def test_owner_can_still_delete_server_when_ownership_unchanged(
    server_manager, two_users
):
    """Guard against over-hardening: the legitimate owner delete still works."""
    owner, _other = two_users
    server = server_manager.create_server(owner.id, "Normal delete server")

    assert server_manager.delete_server(owner.id, server.id) is True
    row = server_manager._db.fetch_one(
        "SELECT deleted FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert row is not None
    assert int(row["deleted"]) == 1
