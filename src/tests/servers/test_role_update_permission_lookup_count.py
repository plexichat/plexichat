"""The role-update hot path builds the editor permission dict only once.

``update_role`` used to call ``get_permissions(user, server)`` directly twice
for the same channel-less key: once to feed the hierarchy decision and again
for the ``check_permissions_grantable`` guard. Both reads are request-invariant
(no role/membership write happens between them), so the second call is a
redundant walk of the permission cache (memory lookup, possible Valkey round
trip and, on a cold key, a recompute of owner/member/role rows).

This test counts the handler's ``get_permissions`` calls with a real server
fixture and records the count seen by each of the two gates. It fails before
the fix (the second gate sees an extra lookup) and passes after hoisting the
invariant lookup into one local, while asserting the resulting role is
unchanged. The separate ``require_permission`` gate is intentionally left
alone: it is a distinct cached verdict check, not one of the duplicated
permission-dict builds.
"""

import pytest


@pytest.fixture
def _permission_lookup_counter(monkeypatch, server_with_members):
    """Count ``get_permissions`` calls made by the role handler during update."""
    server, owner, admin_user, member_user, outsider, admin_role, servers = (
        server_with_members
    )
    handler = servers.role_handler
    real = handler.get_permissions
    calls = []
    gate_counts = {}

    def counting(user_id, server_id, channel_id=None):
        calls.append((user_id, server_id, channel_id))
        return real(user_id, server_id, channel_id)

    monkeypatch.setattr(handler, "get_permissions", counting)

    import src.core.servers.handlers.roles.crud as crud

    real_decision = crud.role_edit_decision
    real_grantable = crud.check_permissions_grantable

    def decision(*args, **kwargs):
        gate_counts["decision"] = len(calls)
        return real_decision(*args, **kwargs)

    def grantable(*args, **kwargs):
        gate_counts["grantable"] = len(calls)
        return real_grantable(*args, **kwargs)

    monkeypatch.setattr(crud, "role_edit_decision", decision)
    monkeypatch.setattr(crud, "check_permissions_grantable", grantable)
    return server, owner, admin_role, servers, calls, gate_counts


def test_update_role_resolves_editor_permissions_once(_permission_lookup_counter):
    server, owner, admin_role, servers, calls, gate_counts = _permission_lookup_counter

    updated = servers.update_role(
        owner.id,
        admin_role.id,
        permissions={"roles.manage": True, "messages.send": False},
    )

    # The two invariant gates must share one permission-dict build: the
    # grantability gate may not trigger a second lookup after the hierarchy
    # decision already resolved the same (user, server, channel=None) key.
    assert gate_counts["grantable"] == gate_counts["decision"]
    # Total calls for the update: one from the cached require_permission gate
    # plus exactly one shared lookup for the two invariant gates. Before the
    # fix this was 3 (require_permission + two duplicate lookups).
    assert len(calls) == 2
    assert calls[0][:2] == (owner.id, server.id)
    assert calls[-1][:2] == (owner.id, server.id)
    assert calls[-1][2] is None
    # Behavior is unchanged: both bits landed on the role.
    assert updated.permissions.get("roles.manage") is True
    assert updated.permissions.get("messages.send") is False
