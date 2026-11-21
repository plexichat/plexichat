"""RBAC mutations must share the permission-guard advisory namespaces.

Namespaces 142 (delete_event / ``events.manage``) and 143 (update_server /
``server.manage``) are held across a gated write's authoritative
``require_permission`` re-check. Every mutation that can invalidate those gates
must take the same locks (in the fixed 142 -> 143 order) or a revocation could
commit in the gap between re-check and write.

These tests model PostgreSQL advisory locks over the SQLite test database: the
simulator hands out (namespace, server_id) locks to a named owner and refuses a
second owner's acquisition while the first holds it. This makes the serialization
interleaving deterministic and fails if a mutation forgets to take the locks.
"""

import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.core.servers.exceptions import PermissionDeniedError


def _setup_manager_with_actor_role(server_manager, two_users):
    owner, actor = two_users
    server = server_manager.create_server(owner.id, "RBAC serialization server")
    server_manager.add_member(server.id, actor.id)
    role = server_manager.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Scoped manager",
        permissions={
            "administrator": False,
            "events.manage": True,
            "server.manage": True,
        },
    )
    server_manager.assign_role(owner.id, server.id, actor.id, role.id)
    return server, owner, actor, role


def _make_event(server_manager, owner, server, name):
    return server_manager.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name=name,
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=server_manager.ScheduledEventType.EXTERNAL,
        location="Somewhere",
    )


@contextmanager
def _pg_advisory_sim(db):
    """Run PG advisory-lock branches against SQLite with deterministic locks.

    All real DB calls are temporarily routed with ``type='sqlite'`` so SQLite
    still handles placeholders and BEGIN/SAVEPOINT; only the manager-level
    ``type`` seen by production code is 'postgres', which is what makes the
    advisory-lock branches execute.
    """
    real_type = db.type
    real_execute = db.execute
    real_execute_many = db.execute_many
    real_begin = db.begin_transaction
    real_commit = db.commit
    real_rollback = db.rollback
    held = {}
    calls = []
    state = {"owner": None}

    class WouldBlock(Exception):
        pass

    outer_type = "postgres"

    def as_sqlite(fn):
        def wrapped(*args, **kwargs):
            db.type = "sqlite"
            try:
                return fn(*args, **kwargs)
            finally:
                db.type = outer_type

        return wrapped

    real_execute_sqlite = as_sqlite(real_execute)
    real_execute_many_sqlite = as_sqlite(real_execute_many)
    real_commit_sqlite = as_sqlite(real_commit)
    real_rollback_sqlite = as_sqlite(real_rollback)

    def release(owner):
        for key in [key for key, who in held.items() if who == owner]:
            del held[key]

    def fake_execute(query, params=None, *args, **kwargs):
        if isinstance(query, str) and "pg_advisory_xact_lock" in query:
            key = tuple(params)
            owner = held.get(key)
            if owner is not None and owner != state["owner"]:
                calls.append(("blocked", state["owner"], key))
                raise WouldBlock(key)
            held[key] = state["owner"]
            calls.append(("acquired", state["owner"], key))
            return SimpleNamespace(rowcount=1)

        # Inject a concurrent change into the window after the gated write's
        # re-check but before its DELETE -- exactly the residual race.
        if (
            state.get("on_delete")
            and isinstance(query, str)
            and query.lstrip().upper().startswith("DELETE FROM SRV_SCHEDULED_EVENTS")
        ):
            callback = state.pop("on_delete")
            callback()

        return real_execute_sqlite(query, params, *args, **kwargs)

    def fake_begin():
        db.type = "sqlite"
        try:
            return real_begin()
        finally:
            db.type = outer_type

    def fake_commit():
        release(state["owner"])
        return real_commit_sqlite()

    def fake_rollback():
        release(state["owner"])
        return real_rollback_sqlite()

    db.type = "postgres"
    db.execute = fake_execute
    db.execute_many = real_execute_many_sqlite
    db.begin_transaction = fake_begin
    db.commit = fake_commit
    db.rollback = fake_rollback
    try:
        yield state, calls, WouldBlock
    finally:
        db.type = real_type
        db.execute = real_execute
        db.execute_many = real_execute_many
        db.begin_transaction = real_begin
        db.commit = real_commit
        db.rollback = real_rollback


def test_role_revocation_serializes_with_gated_write(server_manager, two_users):
    server, owner, actor, role = _setup_manager_with_actor_role(
        server_manager, two_users
    )
    event = _make_event(server_manager, owner, server, "Guarded event")
    later_event = _make_event(server_manager, owner, server, "Later event")
    assert server_manager.has_permission(actor.id, server.id, "events.manage") is True

    revocation = {"blocked": False}
    revoked_permissions = {
        "administrator": False,
        "events.manage": False,
        "server.manage": True,
    }

    with _pg_advisory_sim(server_manager._db) as sim:
        state, calls, WouldBlock = sim
        state["owner"] = "writer"

        def inject_revocation():
            previous = state["owner"]
            state["owner"] = "mutation"
            try:
                server_manager.update_role(
                    owner.id, role.id, permissions=revoked_permissions
                )
            except WouldBlock:
                revocation["blocked"] = True
            finally:
                state["owner"] = previous

        state["on_delete"] = inject_revocation
        assert server_manager.delete_scheduled_event(actor.id, event.id) is True

    # The revocation could not commit while the gated write held namespace 142.
    assert revocation["blocked"] is True, (
        "concurrent role change was not serialized behind the gated write"
    )
    assert ("acquired", "writer", (142, int(server.id) & 0x3FFFFFFF)) in calls
    assert server_manager.has_permission(actor.id, server.id, "events.manage") is True
    assert server_manager.get_scheduled_event(event.id, actor.id) is None

    # Once the writer released the lock, the same revocation succeeds and a
    # subsequent gated write is denied by its re-check.
    server_manager.update_role(owner.id, role.id, permissions=revoked_permissions)
    assert server_manager.has_permission(actor.id, server.id, "events.manage") is False
    with pytest.raises(PermissionDeniedError):
        server_manager.delete_scheduled_event(actor.id, later_event.id)


@pytest.mark.parametrize(
    "mutation",
    [
        "update_role",
        "delete_role",
        "assign_role",
        "remove_role",
        "assign_roles_from_whitelist",
    ],
)
def test_rbac_mutations_acquire_shared_locks_in_order(
    server_manager, three_users, mutation
):
    owner, member, _outsider = three_users
    server = server_manager.create_server(owner.id, "RBAC lock server")
    server_manager.add_member(server.id, member.id)
    role = server_manager.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Scoped manager",
        permissions={"administrator": False, "events.manage": True},
    )
    expected = [
        ("acquired", "mutation", (142, int(server.id) & 0x3FFFFFFF)),
        ("acquired", "mutation", (143, int(server.id) & 0x3FFFFFFF)),
    ]

    with _pg_advisory_sim(server_manager._db) as sim:
        state, calls, _WouldBlock = sim
        state["owner"] = "mutation"

        if mutation == "update_role":
            server_manager.update_role(owner.id, role.id, name="Renamed")
        elif mutation == "delete_role":
            server_manager.delete_role(owner.id, role.id)
        elif mutation == "assign_role":
            server_manager.assign_role(owner.id, server.id, member.id, role.id)
        elif mutation == "remove_role":
            server_manager.assign_role(owner.id, server.id, member.id, role.id)
            calls.clear()
            server_manager.remove_role(owner.id, server.id, member.id, role.id)
        else:
            server_manager.role_handler.assign_roles_from_whitelist(
                member.id, server.id, [role.id], [role.id]
            )

    assert calls == expected, (
        f"{mutation} did not acquire both permission-guard locks in fixed order"
    )
