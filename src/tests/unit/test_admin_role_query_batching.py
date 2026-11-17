"""Query-count regressions for admin role-management routes.

The mutating role routes used to resolve hierarchy (actor position, target
admin position, target role position/system flag) with up to five separate
``fetch_one`` calls.  ``_load_hierarchy`` folds all of that into a single
scalar-subquery statement; these tests pin both the query count and the fact
that the batched decisions still match the canonical permission helpers.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes.admin import roles as role_routes
from src.core.admin import permissions as perm_core


class _CountingDB:
    """Minimal DB double that records every lookup."""

    def __init__(self, hierarchy, existing_assignment=None):
        self.hierarchy = hierarchy
        self.existing_assignment = existing_assignment
        self.fetch_calls = []
        self.execute_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        if "AS current_pos" in sql:
            return self.hierarchy
        if "admin_role_assignments" in sql:
            return self.existing_assignment
        raise AssertionError(f"unexpected fetch_one: {sql}")

    def fetch_all(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return []

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params)))


def _hierarchy_row(
    current=100, target=50, exists=1, role_pos=10, system=0, tuple_row=False
):
    permissions = "{}"
    if tuple_row:
        return (current, target, exists, role_pos, system, permissions)
    return {
        "current_pos": current,
        "target_pos": target,
        "admin_exists": exists,
        "role_pos": role_pos,
        "role_is_system": system,
        "role_permissions": permissions,
    }


def _request():
    return SimpleNamespace(client=None)


def _patch_gate(monkeypatch, admin_id=1):
    monkeypatch.setattr(role_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(role_routes, "_require_role_manager", lambda request: admin_id)
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)


@pytest.mark.unit
def test_load_hierarchy_uses_one_query_and_maps_both_row_shapes():
    for tuple_row in (False, True):
        db = _CountingDB(_hierarchy_row(tuple_row=tuple_row))
        result = role_routes._load_hierarchy(db, 1, 2, 3)

        assert len(db.fetch_calls) == 1
        assert db.fetch_calls[0][1] == (1, 2, 2, 3, 3, 3)
        assert result == {
            "current_pos": 100,
            "target_pos": 50,
            "admin_exists": True,
            "role_pos": 10,
            "role_is_system": False,
            "role_permissions": "{}",
        }


@pytest.mark.unit
def test_load_hierarchy_absent_role_is_reported_as_none():
    db = _CountingDB(_hierarchy_row(role_pos=None))
    result = role_routes._load_hierarchy(db, 1, 2, 3)
    assert result["role_pos"] is None
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_load_create_role_context_batches_position_and_name_check():
    class _Db:
        def __init__(self):
            self.calls = []

        def fetch_one(self, sql, params=()):
            self.calls.append((sql, tuple(params)))
            return {"max_pos": 42, "name_taken": 1}

    db = _Db()
    assert role_routes._load_create_role_context(db, 7, "mod") == (42, True)
    assert len(db.calls) == 1
    assert db.calls[0][1] == (7, "mod")


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,target_pos,current_id,target_id",
    [
        (100, 50, 1, 2),
        (50, 100, 1, 2),
        (50, 50, 1, 2),
        (100, 100, 7, 7),
        (0, 0, 1, 2),
    ],
)
def test_admin_hierarchy_decision_matches_core_helper(
    monkeypatch, current_pos, target_pos, current_id, target_id
):
    monkeypatch.setattr(
        perm_core,
        "get_admin_max_position",
        lambda db, aid: current_pos if aid == current_id else target_pos,
    )
    expected = perm_core.can_manage_admin(object(), current_id, target_id)
    assert (
        role_routes._can_manage_admin_at(current_id, target_id, current_pos, target_pos)
        is expected
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,role_pos,is_system",
    [
        (100, 50, False),
        (50, 50, False),
        (50, 100, False),
        (100, 0, True),
        (99, 0, True),
        (100, None, False),
    ],
)
def test_role_hierarchy_decision_matches_core_helper(
    monkeypatch, current_pos, role_pos, is_system
):
    row = (
        None
        if role_pos is None
        else {"position": role_pos, "is_system": int(is_system)}
    )

    class _Db:
        def fetch_one(self, sql, params=()):
            return row

    monkeypatch.setattr(
        perm_core, "get_admin_max_position", lambda db, aid: current_pos
    )
    expected = perm_core.can_manage_role(_Db(), 1, 3)
    assert role_routes._can_manage_role_at(current_pos, role_pos, is_system) is expected


@pytest.mark.unit
def test_assign_role_hierarchy_is_a_single_lookup(monkeypatch):
    db = _CountingDB(_hierarchy_row(), existing_assignment=None)
    _patch_gate(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)

    assignment = role_routes.RoleAssignment(admin_id=2, role_id=3)
    asyncio.run(role_routes.assign_role(_request(), assignment))

    hierarchy_queries = [c for c in db.fetch_calls if "AS current_pos" in c[0]]
    assert len(hierarchy_queries) == 1
    # The role's permission payload must NOT trigger a second SELECT; it is
    # carried by the hierarchy query. Only the actor-scopes lookup and the
    # existing-assignment check remain.
    assert not any(
        sql.strip().startswith("SELECT permissions FROM admin_roles")
        for sql, _ in db.fetch_calls
    )
    assert len(db.fetch_calls) == 3
    assert any(
        "INSERT INTO admin_role_assignments" in sql for sql, _ in db.execute_calls
    )


@pytest.mark.unit
def test_revoke_role_hierarchy_is_a_single_lookup(monkeypatch):
    db = _CountingDB(_hierarchy_row(), existing_assignment={"admin_id": 2})
    _patch_gate(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)

    asyncio.run(role_routes.revoke_role(_request(), 2, 3))

    hierarchy_queries = [c for c in db.fetch_calls if "AS current_pos" in c[0]]
    assert len(hierarchy_queries) == 1
    assert len(db.fetch_calls) == 2
    assert any(
        "DELETE FROM admin_role_assignments" in sql for sql, _ in db.execute_calls
    )
