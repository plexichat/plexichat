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
route in the two security route modules and asserts each carries the gate. It
also pins every call that consumes the caller-supplied target id, so a new
ungated mutation added under a different name is still caught, and it only
counts gate calls on the handler's own execution path (a gate buried in an
uninvoked nested helper is dead evidence and does not silence the guard).
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

# Id-plumbing calls that legitimately receive the target id but are not
# mutations, so they are excluded from the target-id mutation sweep below.
_UID_PASSTHROUGH_CALLS = {"str", "int", "get_user_details"}

# Every call in these two modules that consumes the caller-supplied target id
# (``uid``) is swept below. The three lifecycle mutations listed here are
# reviewed as deliberately NOT hierarchy-gated: forcing a username change or
# cancelling/delaying a scheduled deletion does not disable or demote the
# target. Every other target-id mutation must appear in _TARGET_MUTATIONS and
# carry the full gate. Pinning the whole set makes a newly added target-id
# mutation (under any name) fail the sweep until it is classified here.
_REVIEWED_UNGATED_UID_MUTATIONS = {
    ("lifecycle.py", "admin_force_username_change", "force_username_change"),
    ("lifecycle.py", "admin_cancel_account_deletion", "cancel_account_deletion"),
    ("lifecycle.py", "admin_delay_account_deletion", "delay_account_deletion"),
}

_EXPECTED_UID_MUTATIONS = {
    ("security.py", "force_logout", "logout_all"),
    ("security.py", "admin_lock_user", "lock_user"),
    ("security.py", "admin_unlock_user", "unlock_user"),
    ("lifecycle.py", "admin_force_purge_account", "force_purge_account"),
} | _REVIEWED_UNGATED_UID_MUTATIONS


def _own_calls(node):
    """Yield Call nodes that execute on ``node``'s own path.

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a helper that calls a gate
    does not run it. Counting a dead nested gate call as protection lets an
    admin-targeting route silence this invariant with fake evidence -- the same
    vacuity already fixed in the cached-route and generic-decorator guards.
    """
    stack = [node]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                yield child
            stack.append(child)


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_names(node):
    """Names of calls that execute on ``node``'s own path."""
    return {name for call in _own_calls(node) if (name := _call_name(call))}


def _uid_mutations(node):
    """Names of calls that consume the caller-supplied target id (``uid``)."""
    return {
        name
        for call in _own_calls(node)
        if (name := _call_name(call))
        and name not in _GATE_CALLS
        and name not in _UID_PASSTHROUGH_CALLS
        and any(isinstance(a, ast.Name) and a.id == "uid" for a in call.args)
    }


def _functions_in(source):
    """Route-handler-shaped defs: module functions and methods, not nested defs.

    Class methods (the lifecycle mixin handlers) must be included, but a helper
    defined *inside* a handler is not itself a route and is skipped -- it is
    reached, if at all, only through its parent handler.
    """
    result = []
    stack = [(ast.parse(source), False)]
    while stack:
        parent, inside_function = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not inside_function:
                    result.append(child)
                stack.append((child, True))
            elif isinstance(child, ast.Lambda):
                stack.append((child, True))
            else:
                stack.append((child, inside_function))
    return result


def _sweep(nodes, label):
    """Apply the gate check to ``nodes``; return (gated, uid_mutations).

    Gate evidence must sit on the handler's own execution path -- a gate call
    in a nested helper/lambda is dead and does not count.
    """
    gated, uid_mutations = set(), set()
    for handler in nodes:
        calls = _call_names(handler)
        mutations = calls & _TARGET_MUTATIONS
        if mutations:
            gated.add((label, handler.name))
            missing = _GATE_CALLS - calls
            assert not missing, (
                f"{label}:{handler.name} calls {sorted(mutations)} "
                f"without hierarchy gate calls {sorted(missing)}"
            )
        uid_mutations |= {
            (label, handler.name, name) for name in _uid_mutations(handler)
        }
    return gated, uid_mutations


def test_every_admin_targeting_route_is_hierarchy_gated():
    """Definitive sweep of the two security modules.

    Finds every route handler that invokes an admin-id-targeting mutation and
    asserts it loads the hierarchy, checks the position with self-refusal, and
    re-asserts at write time. Independently, every call consuming the
    caller-supplied target id is swept and pinned: a newly added target-id
    mutation under any name fails the sweep until it is classified (gated or
    explicitly reviewed as benign). Both equality assertions keep the
    enumeration non-vacuous.
    """
    gated, uid_mutations = set(), set()
    for label, path in (
        ("security.py", _SECURITY_SRC),
        ("lifecycle.py", _LIFECYCLE_SRC),
    ):
        found_gated, found_uid = _sweep(_functions_in(path.read_text()), label)
        gated |= found_gated
        uid_mutations |= found_uid

    assert gated == _EXPECTED_GATED_HANDLERS, (
        f"unexpected admin-targeting route set: {sorted(gated)}"
    )
    assert uid_mutations == _EXPECTED_UID_MUTATIONS, (
        f"unexpected target-id mutation set: {sorted(uid_mutations)}; "
        "classify any new mutation as gated or reviewed-benign"
    )


def test_gate_detector_is_not_vacuous():
    """Synthetic handlers prove the detector fires and recognises live gates."""
    ungated = """
async def bad(request, body):
    uid = int(body.user_id)
    auth.logout_all(uid)
"""
    with pytest.raises(AssertionError, match="without hierarchy gate calls"):
        _sweep(_functions_in(ungated), "synthetic.py")

    gated = """
async def good(request, body):
    uid = int(body.user_id)
    hierarchy = _load_admin_hierarchy(db, uid, uid)
    if not _can_manage_admin_at(uid, uid, hierarchy["current_pos"], hierarchy["target_pos"]):
        raise HTTPException(status_code=403)
    _assert_hierarchy_at_write(db, uid, uid)
    auth.logout_all(uid)
"""
    found, uid_mutations = _sweep(_functions_in(gated), "synthetic.py")
    assert found == {("synthetic.py", "good")}
    assert uid_mutations == {("synthetic.py", "good", "logout_all")}


def test_dead_nested_gate_calls_do_not_protect_route():
    """Gate calls in a never-invoked nested callable must NOT satisfy the guard.

    Regression: the original detector used ``ast.walk``, which descends into
    nested functions and lambdas. An admin-targeting route could invoke its
    mutation with no real gate yet pass the sweep by defining a helper (or
    lambda) that merely mentions the three gate calls.
    """
    source = """
async def nested(request, body):
    uid = int(body.user_id)
    def _fake_gate():
        _load_admin_hierarchy(db, uid, uid)
        _can_manage_admin_at(uid, uid, 0, 0)
        _assert_hierarchy_at_write(db, uid, uid)
    auth.logout_all(uid)

async def lambda_gate(request, body):
    uid = int(body.user_id)
    _ = lambda: (
        _load_admin_hierarchy(db, uid, uid),
        _can_manage_admin_at(uid, uid, 0, 0),
        _assert_hierarchy_at_write(db, uid, uid),
    )
    admin.lock_user(uid, 60)
"""
    for handler in _functions_in(source):
        with pytest.raises(AssertionError, match="without hierarchy gate calls"):
            _sweep([handler], "synthetic.py")
