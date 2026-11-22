"""Admin session/lock actions must bind the target id with a strict-int gate.

``POST /admin/security/force-logout``, ``/lock-user`` and ``/unlock-user``
used a bare ``int(body.user_id)`` on the caller-supplied target id before any
write. ``int()`` launders a malformed-but-coercible token into a *different*
target: ``True``->1, ``False``->0, ``5.9``->5, ``"٤٢"``->42, ``"1_2"``->12.
The ``SnowflakeID`` schema rejects those on the normal HTTP path, but a
directly constructed body / internal caller bypasses that validation, so the
laundered id reached ``auth.logout_all`` / ``admin.lock_user`` /
``admin.unlock_user`` and the ``system_alerts`` INSERT -- a session revocation
or account lock aimed at a different user than the caller supplied.

Every other admin id-boundary (force-purge, tier change, role assign/revoke,
data-export approve/deny, admin-users family) funnels through ``parse_id``.
These three handlers were the exception.  The fix replaces the bare ``int()``
with ``parse_id`` so a non-int-shaped token is rejected with 400 before any
write.

Tests are non-vacuous: a live mock DB and patched core mutators record the
exact write targets, so the positive control proves the harness reaches the
write with the genuine id, and every hostile id must be refused with *no*
write.  An AST ratchet stops bare ``int()`` from creeping back, with a
synthetic detector self-test.
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
import src.core.admin as admin_core
import src.core.auth as auth_core
from src.api.routes.admin import security

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SECURITY_SRC = _REPO_ROOT / "src" / "api" / "routes" / "admin" / "security.py"

HOSTILE_IDS = [
    True,  # bool -> 1
    False,  # bool -> 0
    5.9,  # float truncates to 5
    "٤٢",  # Arabic-Indic homoglyph -> 42
    "1_2",  # PEP 515 separator -> 12
]
HANDLERS = ["force_logout", "admin_lock_user", "admin_unlock_user"]


class _Cursor:
    rowcount = 1


class _DB:
    """Records every write so a hostile id can be proven to reach none of them."""

    def __init__(self):
        self.executed = []
        self.row = {
            "target_exists": False,
            "current_pos": 0,
            "target_pos": 0,
            "account_locked": 0,
        }

    def fetch_one(self, sql, params=()):
        return self.row

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor()


class _Core:
    """Records the destructive mutator calls with their exact target id."""

    def __init__(self):
        self.logged_out = []
        self.locked = []
        self.unlocked = []

    def logout_all(self, uid, **kwargs):
        self.logged_out.append(uid)
        return 1

    def lock_user(self, uid, duration):
        self.locked.append((uid, duration))

    def unlock_user(self, uid):
        self.unlocked.append(uid)


def _req() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _body(uid):
    # Directly constructed body: bypasses Pydantic SnowflakeID validation, the
    # exact scenario that made the old bare int() a laundering boundary.
    return SimpleNamespace(user_id=uid, duration_seconds=None)


def _patch(monkeypatch, db, core, actor_id=10):
    monkeypatch.setattr(security, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(security, "require_admin_permission", lambda r, p: actor_id)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(auth_core, "logout_all", core.logout_all)
    monkeypatch.setattr(admin_core, "lock_user", core.lock_user)
    monkeypatch.setattr(admin_core, "unlock_user", core.unlock_user)

    async def _no_sockets(*args, **kwargs):
        return None

    monkeypatch.setattr(
        security, "_close_revoked_user_gateway_connections", _no_sockets
    )


def _writes(db, core, handler):
    """All side effects a handler can perform, keyed by handler under test."""
    return {
        "force_logout": list(core.logged_out),
        "admin_lock_user": list(core.locked),
        "admin_unlock_user": list(core.unlocked),
    }[handler] + list(db.executed)


@pytest.mark.parametrize("handler", HANDLERS)
def test_harness_reaches_the_write(monkeypatch, handler):
    """Non-vacuity control: a genuine int id reaches a write with that id."""
    db, core = _DB(), _Core()
    _patch(monkeypatch, db, core)
    fn = getattr(security, handler)
    result = asyncio.run(fn(_req(), _body(42)))
    assert result.success is True
    writes = _writes(db, core, handler)
    assert len(writes) >= 1, "positive control never reached a write"
    if handler == "force_logout":
        assert core.logged_out == [42]
    elif handler == "admin_lock_user":
        assert core.locked == [(42, None)]
    else:
        assert core.unlocked == [42]


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("bad", HOSTILE_IDS, ids=repr)
def test_hostile_id_rejected_before_any_write(monkeypatch, handler, bad):
    db, core = _DB(), _Core()
    _patch(monkeypatch, db, core)
    fn = getattr(security, handler)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fn(_req(), _body(bad)))
    assert exc.value.status_code == 400
    assert _writes(db, core, handler) == [], "a hostile id reached a write"


# ---------------------------------------------------------------------------
# AST ratchet: each handler must bind uid through parse_id, never int().
# ---------------------------------------------------------------------------


def _handler_node(tree, name):
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
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
        async def force_logout(request, body):
            uid = int(body.user_id)
        """
    )
    return _assignment_calls(_handler_node(ast.parse(old), "force_logout"), "int")


def test_detector_is_not_vacuous():
    assert "uid" in _detector_flags_bare_int()


@pytest.mark.parametrize("handler", HANDLERS)
def test_handler_binds_uid_through_parse_id(handler):
    tree = ast.parse(_SECURITY_SRC.read_text(encoding="utf-8"))
    node = _handler_node(tree, handler)
    assert "uid" not in _assignment_calls(node, "int")
    assert "uid" in _assignment_calls(node, "parse_id")
