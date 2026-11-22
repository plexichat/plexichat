"""Admin role assignment must bind both target ids with a strict-int gate.

``POST /admin/roles/assign`` used a bare ``int()`` on the two caller-supplied
ids (``assignment.admin_id`` / ``assignment.role_id``):

    target_admin_id = int(assignment.admin_id)
    target_role_id = int(assignment.role_id)

``int()`` launders malformed-but-coercible tokens into a *different* target:
``True``->1, ``False``->0, ``5.9``->5, ``"٤٢"``->42, ``"1_2"``->12.
The SnowflakeID schema rejects those on the normal HTTP path, but a directly
constructed body (or an internal caller) bypasses that validation, so the
laundered id reached the ``INSERT INTO admin_role_assignments`` -- a privilege
grant aimed at a different admin/role than the caller read from the wire.

Every other admin id-boundary (force-purge, tier change, force-password-change,
data-export approve/deny, admin-users family) funnels through ``parse_id``; this
handler was the lone exception.  The fix replaces both coercions with
``parse_id`` so a non-int-shaped token is rejected with 400 before any write.

Tests are non-vacuous: a live mock DB records the exact INSERT parameters, so
the positive control proves the harness reaches the write, and every hostile
id must be refused with *no* INSERT.  An AST ratchet stops bare ``int()`` from
creeping back, with a synthetic detector self-test.
"""

import ast
import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.admin import roles
import src.core.admin.permissions as perms

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROLES_SRC = _REPO_ROOT / "src" / "api" / "routes" / "admin" / "roles.py"

HOSTILE_IDS = [
    True,  # bool -> 1
    False,  # bool -> 0
    5.9,  # float truncates to 5
    "٤٢",  # Arabic-Indic homoglyph -> 42
    "1_2",  # PEP 515 separator -> 12
]


class _Cursor:
    rowcount = 1


class _DB:
    """Records every write so the test can prove nothing reached the DB."""

    def __init__(self):
        self.assign_inserts = []

    def fetch_one(self, sql, params=()):
        return None

    def execute(self, sql, params=()):
        if "INSERT INTO admin_role_assignments" in " ".join(sql.split()):
            self.assign_inserts.append(tuple(params))
        return _Cursor()


def _req():
    return cast(Request, SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))


def _patch(monkeypatch, db, actor=10):
    """Bypass auth and let every hierarchy/grantability gate pass."""
    monkeypatch.setattr(roles, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(roles, "_require_role_manager", lambda r: actor)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(
        roles,
        "_load_hierarchy",
        lambda db, a, t, rid: {
            "role_pos": 1,
            "admin_exists": True,
            "current_pos": 50,
            "target_pos": 1,
            "role_is_system": False,
            "role_permissions": '{"admin.read": true}',
        },
    )
    monkeypatch.setattr(roles, "_can_manage_admin_at", lambda *a: True)
    monkeypatch.setattr(roles, "_can_manage_role_at", lambda *a: True)
    monkeypatch.setattr(perms, "can_delegate_permissions", lambda *a: True)
    monkeypatch.setattr(perms, "log_admin_action", lambda *a, **k: None)
    monkeypatch.setattr(roles, "_hierarchy_write_guard", lambda *a: ("1=1", ()))
    monkeypatch.setattr(roles, "invalidate_privilege_caches", lambda: None)
    monkeypatch.setattr(roles, "invalidate_admin_read_caches", lambda: None)


def _body(admin_id, role_id):
    # Directly constructed body: bypasses Pydantic SnowflakeID validation, the
    # exact scenario that made the old bare int() a laundering boundary.
    return SimpleNamespace(admin_id=admin_id, role_id=role_id)


def test_harness_reaches_the_write(monkeypatch):
    """Non-vacuity control: a genuine int admin/role id produces an INSERT."""
    db = _DB()
    _patch(monkeypatch, db)
    asyncio.run(roles.assign_role(_req(), _body(42, 7)))
    assert len(db.assign_inserts) == 1
    assert db.assign_inserts[0][0] == 42
    assert db.assign_inserts[0][1] == 7


@pytest.mark.parametrize("bad", HOSTILE_IDS, ids=repr)
@pytest.mark.parametrize("field", ["admin_id", "role_id"])
def test_hostile_id_rejected_before_any_grant_write(monkeypatch, field, bad):
    db = _DB()
    _patch(monkeypatch, db)
    body = _body(bad, 7) if field == "admin_id" else _body(42, bad)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(roles.assign_role(_req(), body))
    assert exc.value.status_code == 400
    assert db.assign_inserts == [], "a hostile id reached the privilege grant"


# ---------------------------------------------------------------------------
# AST ratchet: the handler must bind both ids through parse_id, never int().
# ---------------------------------------------------------------------------


def _assign_role_node(tree):
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "assign_role"
    )


def _assignment_calls(node, name):
    """Return assignment targets assigned from a call to ``name``."""
    found = set()
    for stmt in ast.walk(node):
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
            continue
        fn = stmt.value.func
        called = (isinstance(fn, ast.Name) and fn.id == name) or (
            isinstance(fn, ast.Attribute) and fn.attr == name
        )
        if not called:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                found.add(target.id)
    return found


def _detector_flags_bare_int():
    old = textwrap.dedent(
        """
        async def assign_role(request, assignment):
            target_admin_id = int(assignment.admin_id)
            target_role_id = int(assignment.role_id)
        """
    )
    node = _assign_role_node(ast.parse(old))
    return _assignment_calls(node, "int")


def test_detector_is_not_vacuous():
    assert {"target_admin_id", "target_role_id"} <= _detector_flags_bare_int()


def test_assign_role_binds_both_ids_through_parse_id():
    tree = ast.parse(_ROLES_SRC.read_text(encoding="utf-8"))
    node = _assign_role_node(tree)
    int_targets = _assignment_calls(node, "int")
    parse_targets = _assignment_calls(node, "parse_id")
    assert "target_admin_id" not in int_targets
    assert "target_role_id" not in int_targets
    assert {"target_admin_id", "target_role_id"} <= parse_targets
