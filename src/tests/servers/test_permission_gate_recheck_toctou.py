"""Stale-state regression for permission-based check-then-write sites.

``delete_event`` (``events.manage``) and ``update_server`` (``server.manage``)
gate on ``require_permission`` -- role JSON + owner shortcut + channel
overrides -- which cannot be re-asserted with a WHERE predicate the way the
creator/owner guards can.  Both keep the original early gate, but now re-run
``require_permission`` under a per-server advisory lock immediately before the
mutating write.

These tests land a concurrent role revocation in the gap between the original
check and the write.  Before the fix the single check left the write running
with revoked authority; after the fix the re-check denies it.  The trailing
"unchanged" tests guard against over-hardening.
"""

import time

import pytest

from src.core.servers.exceptions import PermissionDeniedError


def _setup_manager_with_actor_role(server_manager, two_users, permissions):
    owner, actor = two_users
    server = server_manager.create_server(owner.id, "Permission TOCTOU server")
    server_manager.add_member(server.id, actor.id)
    role = server_manager.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Scoped manager",
        permissions=permissions,
    )
    server_manager.assign_role(owner.id, server.id, actor.id, role.id)
    return server, owner, actor, role


def _revoke_in_gap(server_manager, permission, owner, role, remaining_permissions):
    """Patch ``require_permission`` so a revocation lands after the first check.

    The first check sees the still-valid grant (it must, or we would never
    reach the gap).  On that first call we revoke the permission via the real
    ``update_role`` path, which also invalidates every permission cache tier,
    so the authoritative re-check sees the revoked state.  Nested
    ``roles.manage`` checks performed by ``update_role`` are passed through
    untouched.  Returns the original callable (for restoration) and a counter.
    """
    real_require = server_manager.require_permission
    calls = {"n": 0}

    def racing_require(user_id, server_id, perm, channel_id=None):
        if perm == permission:
            calls["n"] += 1
            if calls["n"] == 1:
                result = real_require(user_id, server_id, perm, channel_id)
                server_manager.update_role(
                    user_id=owner.id,
                    role_id=role.id,
                    permissions=remaining_permissions,
                )
                return result
        return real_require(user_id, server_id, perm, channel_id)

    server_manager.require_permission = racing_require
    return real_require, calls


def _make_event(server_manager, owner, server):
    return server_manager.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name="Revoked-authority event",
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=server_manager.ScheduledEventType.EXTERNAL,
        location="Somewhere",
    )


def test_delete_event_denied_when_permission_revoked_in_gap(server_manager, two_users):
    server, owner, actor, role = _setup_manager_with_actor_role(
        server_manager,
        two_users,
        {"administrator": False, "events.manage": True, "server.manage": True},
    )
    event = _make_event(server_manager, owner, server)
    # Warm the grant: the original check must be a genuine allow.
    assert server_manager.has_permission(actor.id, server.id, "events.manage") is True

    real_require, calls = _revoke_in_gap(
        server_manager,
        "events.manage",
        owner,
        role,
        {"administrator": False, "events.manage": False, "server.manage": True},
    )
    try:
        with pytest.raises(PermissionDeniedError):
            server_manager.delete_scheduled_event(actor.id, event.id)
        # Non-vacuous: the fix must actually re-run the gate.
        assert calls["n"] >= 2, "permission was never re-checked before the write"
    finally:
        server_manager.require_permission = real_require

    # The event survives: the actor's authority was revoked before the write.
    assert server_manager.get_scheduled_event(event.id, actor.id) is not None


def test_delete_event_allowed_when_permission_unchanged(server_manager, two_users):
    server, owner, actor, _role = _setup_manager_with_actor_role(
        server_manager,
        two_users,
        {"administrator": False, "events.manage": True, "server.manage": True},
    )
    event = _make_event(server_manager, owner, server)

    assert server_manager.delete_scheduled_event(actor.id, event.id) is True
    assert server_manager.get_scheduled_event(event.id, actor.id) is None


def test_update_server_denied_when_permission_revoked_in_gap(server_manager, two_users):
    server, owner, actor, role = _setup_manager_with_actor_role(
        server_manager,
        two_users,
        {"administrator": False, "events.manage": True, "server.manage": True},
    )
    assert server_manager.has_permission(actor.id, server.id, "server.manage") is True

    real_require, calls = _revoke_in_gap(
        server_manager,
        "server.manage",
        owner,
        role,
        {"administrator": False, "events.manage": True, "server.manage": False},
    )
    try:
        with pytest.raises(PermissionDeniedError):
            server_manager.update_server(actor.id, server.id, name="Hijacked")
        assert calls["n"] >= 2, "permission was never re-checked before the write"
    finally:
        server_manager.require_permission = real_require

    row = server_manager._db.fetch_one(
        "SELECT name FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert row["name"] == "Permission TOCTOU server"


def test_update_server_allowed_when_permission_unchanged(server_manager, two_users):
    server, _owner, actor, _role = _setup_manager_with_actor_role(
        server_manager,
        two_users,
        {"administrator": False, "events.manage": True, "server.manage": True},
    )

    updated = server_manager.update_server(actor.id, server.id, name="Renamed by actor")
    assert updated.name == "Renamed by actor"
    row = server_manager._db.fetch_one(
        "SELECT name FROM srv_servers WHERE id = ?", (server.id,)
    )
    assert row["name"] == "Renamed by actor"
