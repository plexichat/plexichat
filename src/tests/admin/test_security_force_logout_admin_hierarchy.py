"""Force-logout must honour the admin role-position hierarchy.

``POST /admin/security/force-logout`` only checked the ``admin.security.sessions``
scope. It calls ``auth.logout_all(uid)``, invalidating every session for its
target, so it disables the target's access in the same way as ``lock-user``.
Both it and lock/unlock share the very same scope, and every other
admin-targeting mutation (update/delete/toggle-status, ban/unban admin,
force-password-change, force-purge, lock/unlock) refuses a target at or above
the actor's role position. Without a gate here, any holder of that scope could
revoke a superior admin's sessions -- or its own -- bypassing the hierarchy.

These tests pin the gate: an upward/equal/self admin force-logout is refused
before ``auth.logout_all`` runs (and the write-time hierarchy re-assert is
issued for allowed admin targets), while logging out a subordinate admin or an
ordinary user still works. A static guard enumerates every admin-id-targeting
route in the two security route modules and asserts each carries the gate.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
import src.core.auth as auth_core
from src.api.routes.admin import security

pytestmark = [pytest.mark.security, pytest.mark.unit]

ACTOR_ID = 10
TARGET_ID = 100


class _Cursor:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _HierarchyDB:
    """Answers the hierarchy lookup and records every write."""

    def __init__(self, current_pos: int, target_pos: int, target_exists: bool = True):
        self.row = {
            "target_exists": target_exists,
            "current_pos": current_pos,
            "target_pos": target_pos,
            "account_locked": 0,
        }
        self.executed = []

    def fetch_one(self, sql, params=()):
        return self.row

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor(1)


class _AuthCore:
    def __init__(self):
        self.logged_out = []

    def logout_all(self, uid, **kwargs):
        self.logged_out.append(uid)
        return 1


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _body(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id)


def _patch(monkeypatch, db, core, actor_id):
    monkeypatch.setattr(security, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        security, "require_admin_permission", lambda request, perm: actor_id
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(auth_core, "logout_all", core.logout_all)


def _guard_writes(db):
    """Writes that re-assert the hierarchy at write time."""
    return [sql for sql, _ in db.executed if "SET id = id" in sql]


class TestForceLogoutHierarchyGate:
    def test_lower_admin_cannot_force_logout_superior_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=100)
        core = _AuthCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.force_logout(_request(), _body(TARGET_ID)))

        assert exc.value.status_code == 403
        assert core.logged_out == [], "superior admin's sessions were revoked"
        assert _guard_writes(db) == []

    def test_equal_position_cannot_force_logout_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=50, target_pos=50)
        core = _AuthCore()
        _patch(monkeypatch, db, core, actor_id=50)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.force_logout(_request(), _body(51)))

        assert exc.value.status_code == 403
        assert core.logged_out == []

    def test_admin_cannot_force_logout_itself(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=10)
        core = _AuthCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.force_logout(_request(), _body(ACTOR_ID)))

        assert exc.value.status_code == 403
        assert core.logged_out == [], (
            "admin revoked its own sessions via security route"
        )

    def test_super_admin_can_still_force_logout_subordinate_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=100, target_pos=10)
        core = _AuthCore()
        _patch(monkeypatch, db, core, actor_id=100)
        subordinate_id = 42

        asyncio.run(security.force_logout(_request(), _body(subordinate_id)))

        assert core.logged_out == [subordinate_id]
        assert len(_guard_writes(db)) == 1, "write-time hierarchy re-assert missing"

    def test_ordinary_user_target_is_not_hierarchy_gated(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=0, target_exists=False)
        core = _AuthCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        asyncio.run(security.force_logout(_request(), _body(TARGET_ID)))

        assert core.logged_out == [TARGET_ID]
        assert _guard_writes(db) == []


# ---------------------------------------------------------------------------
# Static guard: every admin-id-targeting route must carry the hierarchy gate.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SECURITY_SRC = _REPO_ROOT / "src/api/routes/admin/security.py"
_LIFECYCLE_SRC = _REPO_ROOT / "src/api/routes/admin/users/lifecycle.py"

# Calls that re-assert the "actor outranks target, never self" rule.
_GATE_CALLS = {
    "_load_admin_hierarchy",
    "_can_manage_admin_at",
    "_assert_hierarchy_at_write",
}

# Downstream mutations that act on a caller-supplied target user id. A route
# handler that calls one of these is an admin-id-targeting mutation and MUST
# carry the gate. ``logout_all_users`` (the global purge) is deliberately not
# here: it takes no target id and uses a separate scope.
_TARGET_MUTATIONS = {
    "logout_all",
    "lock_user",
    "unlock_user",
    "force_purge_account",
}

_EXPECTED_GATED_HANDLERS = {
    ("security.py", "force_logout"),
    ("security.py", "admin_lock_user"),
    ("security.py", "admin_unlock_user"),
    ("lifecycle.py", "admin_force_purge_account"),
}


def _call_names(node):
    """All function/method names called anywhere inside ``node``."""
    names = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _route_handlers(path):
    tree = ast.parse(path.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def test_every_admin_targeting_route_is_hierarchy_gated():
    """Definitive sweep of the two security modules.

    Finds every route handler that invokes an admin-id-targeting mutation and
    asserts it loads the hierarchy, checks the position with self-refusal, and
    re-asserts at write time. The equality assertion makes the enumeration
    non-vacuous: adding a new admin-targeting handler here without a gate (or
    removing a known one) fails the test.
    """
    matched = set()
    for label, path in (
        ("security.py", _SECURITY_SRC),
        ("lifecycle.py", _LIFECYCLE_SRC),
    ):
        for handler in _route_handlers(path):
            calls = _call_names(handler)
            if not (calls & _TARGET_MUTATIONS):
                continue
            matched.add((label, handler.name))
            missing = _GATE_CALLS - calls
            assert not missing, (
                f"{label}:{handler.name} calls {sorted(calls & _TARGET_MUTATIONS)} "
                f"without hierarchy gate calls {sorted(missing)}"
            )

    assert matched == _EXPECTED_GATED_HANDLERS, (
        f"unexpected admin-targeting route set: {sorted(matched)}"
    )
