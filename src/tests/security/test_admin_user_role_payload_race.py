"""admin-account role grants must bind the write to the authorized role payload.

The dedicated ``admin/roles.py:assign_role`` binds its INSERT to the exact
``admin_roles.permissions`` value it authorized (compare-and-swap), so a
concurrent widening of the role's scopes between the check and the write cannot
escalate. The parallel role-grant paths in ``admin_users.py`` -- creating an
admin with a role and retargeting an existing admin's role -- only did a
snapshot ``can_grant_existing_role`` check and then wrote with an unguarded
INSERT. A role widened in that gap was attached with its escalated payload.

These tests drive both routes against in-memory SQLite and land the widening
*after* the delegation snapshot is read but *before* the INSERT executes.
"""

import asyncio
import json
import sqlite3
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import src.api as api
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.admin import permissions as perm_core

pytestmark = pytest.mark.unit

BENIGN = json.dumps({"admin.read": True, "admin.edit": True})
WIDENED = json.dumps({"admin.read": True, "admin.edit": True, "config.security": True})
HELD = frozenset({"admin.read", "admin.edit"})

SCHEMA = """
CREATE TABLE auth_users (
  id INTEGER PRIMARY KEY, username TEXT, email TEXT,
  password_hash TEXT, account_locked INTEGER DEFAULT 0
);
CREATE TABLE admin_users (
  id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
  email TEXT, created_at INTEGER, must_setup_otp INTEGER DEFAULT 0
);
CREATE TABLE admin_roles (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT,
  permissions TEXT NOT NULL, created_at INTEGER, created_by INTEGER,
  is_system INTEGER DEFAULT 0, position INTEGER DEFAULT 10
);
CREATE TABLE admin_role_assignments (
  admin_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
  assigned_at INTEGER, assigned_by INTEGER,
  PRIMARY KEY (admin_id, role_id)
);
"""


class _DB:
    """sqlite3-backed double that can widen the grant role at the snapshot read."""

    def __init__(self, widen_at_snapshot: bool):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.widen_at_snapshot = widen_at_snapshot
        self.widened = False

        self.conn.execute(
            "INSERT INTO auth_users (id, username, password_hash, account_locked) "
            "VALUES (1, 'actor', 'h', 0), (2, 'target', 'h', 0), (42, 'newadmin', 'h', 0)"
        )
        self.conn.execute(
            "INSERT INTO admin_users (id, username, password_hash) "
            "VALUES (1, 'actor', 'h'), (2, 'target', 'h')"
        )
        # Actor (position 100) outranks the target (position 50). The role the
        # actor is allowed to hand out sits far below both (position 5).
        self.conn.execute(
            "INSERT INTO admin_roles "
            "(id, name, permissions, created_at, created_by, position) "
            "VALUES (10, 'actor-role', '{}', 0, 1, 100), "
            "(20, 'target-role', '{}', 0, 1, 50), "
            "(30, 'moderator', ?, 0, 1, 5)",
            (BENIGN,),
        )
        self.conn.execute(
            "INSERT INTO admin_role_assignments VALUES (1, 10, 0, 1), (2, 20, 0, 1)"
        )

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params or ()).fetchone()
        q = " ".join(sql.split()).lower()
        if (
            self.widen_at_snapshot
            and not self.widened
            and q == "select permissions from admin_roles where id = ?"
        ):
            # Land the widening immediately AFTER the snapshot row is returned.
            # can_grant_existing_role captures the benign payload from that row,
            # but the authoritative row now carries the escalated payload.
            self.conn.execute(
                "UPDATE admin_roles SET permissions = ? WHERE id = 30", (WIDENED,)
            )
            self.widened = True
        return row

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params or ())

    def assignment(self, admin_id, role_id=30):
        return self.conn.execute(
            "SELECT 1 FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
            (admin_id, role_id),
        ).fetchone()

    def role_permissions(self):
        return self.conn.execute(
            "SELECT permissions FROM admin_roles WHERE id = 30"
        ).fetchone()[0]


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/users",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def _patchers(db):
    return (
        patch.object(au, "check_host_restriction"),
        patch.object(au, "require_admin_permission", return_value=1),
        patch.object(api, "get_db", return_value=db),
        patch.object(
            api,
            "get_auth",
            return_value=SimpleNamespace(register=lambda **kw: SimpleNamespace(id=42)),
        ),
        patch.object(perm_core, "log_admin_action"),
        patch.object(au, "invalidate_privilege_caches"),
        patch.object(perm_core, "get_admin_granted_permissions", return_value=HELD),
    )


def _create(db):
    stack = ExitStack()
    with stack:
        for patcher in _patchers(db):
            stack.enter_context(patcher)
        return asyncio.run(
            _Router().create_admin_user(
                _request(),
                au.AdminUserCreate(
                    username="mod",
                    email="m@example.com",
                    password="pw",
                    role="moderator",
                ),
            )
        )


def _update(db):
    stack = ExitStack()
    with stack:
        for patcher in _patchers(db):
            stack.enter_context(patcher)
        return asyncio.run(
            _Router().update_admin_user(
                _request(), "2", au.AdminUserUpdate(role="moderator")
            )
        )


class TestCreateAdminUserPayloadRace:
    def test_normal_grant_succeeds(self):
        db = _DB(widen_at_snapshot=False)
        _create(db)
        assert db.assignment(42) is not None
        assert db.role_permissions() == BENIGN

    def test_concurrent_widening_is_rejected(self):
        db = _DB(widen_at_snapshot=True)
        with pytest.raises(HTTPException) as exc:
            _create(db)
        assert exc.value.status_code == 409
        # Non-vacuous: the race really landed, and the widened payload really
        # carries a scope the actor never held.
        assert db.widened is True
        assert db.role_permissions() == WIDENED
        assert json.loads(WIDENED)["config.security"] is True
        assert "config.security" not in HELD
        # The escalated role was NOT attached to the newly created admin.
        assert db.assignment(42) is None


class TestUpdateAdminUserPayloadRace:
    def test_normal_retarget_succeeds(self):
        db = _DB(widen_at_snapshot=False)
        _update(db)
        assert db.assignment(2) is not None
        assert db.role_permissions() == BENIGN

    def test_concurrent_widening_is_rejected(self):
        db = _DB(widen_at_snapshot=True)
        with pytest.raises(HTTPException) as exc:
            _update(db)
        assert exc.value.status_code == 409
        assert db.widened is True
        assert db.role_permissions() == WIDENED
        assert json.loads(WIDENED)["config.security"] is True
        assert "config.security" not in HELD
        # The retargeted admin did not receive the escalated role.
        assert db.assignment(2) is None
