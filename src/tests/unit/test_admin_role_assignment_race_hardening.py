"""Regression: privilege-changing role writes re-assert hierarchy at write time.

``update_role``/``delete_role`` already re-check their authorization in the
write's WHERE clause, but ``assign_role``/``revoke_role`` used to grant and
revoke privileges from a stale SELECT snapshot with an unguarded write. A
concurrent admin could escalate a role's scopes, promote it, or elevate the
target admin in that window and the stale decision would still be applied.

These tests drive the real routes against an in-memory SQLite database and
mutate the database *after* the hierarchy snapshot is read but *before* the
write executes, reproducing the race deterministically.
"""

import asyncio
import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeID, SnowflakeIDParam


SCHEMA = """
CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE admin_roles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    permissions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    is_system INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 10
);
CREATE TABLE admin_role_assignments (
    admin_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    assigned_at INTEGER NOT NULL,
    assigned_by INTEGER NOT NULL,
    PRIMARY KEY (admin_id, role_id)
);
"""


class _DB:
    """Thin sqlite3 adapter exposing only what the route layer uses."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        self.after_hierarchy = None
        self._hierarchy_seen = False

    def fetch_one(self, query, params=None):
        row = self.conn.execute(query, params or ()).fetchone()
        if "AS current_pos" in query and not self._hierarchy_seen:
            self._hierarchy_seen = True
            if self.after_hierarchy is not None:
                self.after_hierarchy(self.conn)
        return row

    def fetch_all(self, query, params=None):
        return self.conn.execute(query, params or ()).fetchall()

    def execute(self, query, params=None):
        return self.conn.execute(query, params or ())

    def add_role(self, role_id, name, permissions, position, is_system=0):
        self.conn.execute(
            "INSERT INTO admin_roles "
            "(id, name, description, permissions, created_at, created_by, is_system, position) "
            "VALUES (?, ?, '', ?, 0, 1, ?, ?)",
            (role_id, name, json.dumps(permissions), is_system, position),
        )

    def add_assignment(self, admin_id, role_id):
        self.conn.execute(
            "INSERT INTO admin_role_assignments VALUES (?, ?, 0, 1)",
            (admin_id, role_id),
        )

    def assignments(self):
        return self.conn.execute(
            "SELECT admin_id, role_id FROM admin_role_assignments"
        ).fetchall()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/roles/assign",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def _seed(*, actor_position=50, target_position=10, role_position=5,
          permissions=None, is_system=0):
    db = _DB()
    for admin_id in (1, 2):
        db.conn.execute(
            "INSERT INTO admin_users VALUES (?, ?)", (admin_id, f"admin-{admin_id}")
        )
    db.add_role(20, "actor-role", {"*": True}, actor_position)
    db.add_assignment(1, 20)
    if target_position:
        db.add_role(21, "target-role", {"users.read": True}, target_position)
        db.add_assignment(2, 21)
    db.add_role(
        10,
        "grant-role",
        permissions if permissions is not None else {"users.read": True},
        role_position,
        is_system,
    )
    return db


def _patches(db):
    from src.api.routes.admin import roles as routes

    return ExitStack(), (
        patch.object(routes, "check_host_restriction"),
        patch.object(routes, "_require_role_manager", return_value=1),
        patch("src.api.get_db", return_value=db),
        patch(
            "src.core.admin.permissions.can_delegate_permissions", return_value=True
        ),
        patch.object(routes, "invalidate_privilege_caches"),
        patch("src.core.admin.permissions.log_admin_action"),
    )


def _assign(db):
    from src.api.routes.admin.roles import RoleAssignment, assign_role

    body = RoleAssignment(admin_id=SnowflakeID(2), role_id=SnowflakeID(10))
    stack, patchers = _patches(db)
    with stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        return asyncio.run(assign_role(_request(), body))


def _revoke(db):
    from src.api.routes.admin.roles import revoke_role

    stack, patchers = _patches(db)
    with stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        return asyncio.run(
            revoke_role(_request(), SnowflakeIDParam(2), SnowflakeIDParam(10))
        )


class TestAssignmentWriteGuard:
    def test_normal_assignment_succeeds(self):
        db = _seed()
        _assign(db)
        assert (2, 10) in db.assignments()

    def test_concurrent_scope_escalation_is_rejected(self):
        db = _seed()

        def escalate(conn):
            conn.execute(
                "UPDATE admin_roles SET permissions = ? WHERE id = 10",
                (json.dumps({"config.security": True}),),
            )

        db.after_hierarchy = escalate
        with pytest.raises(HTTPException) as exc:
            _assign(db)
        assert exc.value.status_code == 409
        assert (2, 10) not in db.assignments()

    def test_concurrent_role_promotion_is_rejected(self):
        db = _seed()

        def promote(conn):
            conn.execute("UPDATE admin_roles SET position = 60 WHERE id = 10")

        db.after_hierarchy = promote
        with pytest.raises(HTTPException) as exc:
            _assign(db)
        assert exc.value.status_code == 409
        assert (2, 10) not in db.assignments()

    def test_super_admin_can_still_assign_a_system_role(self):
        db = _seed(actor_position=100, role_position=100, is_system=1)
        _assign(db)
        assert (2, 10) in db.assignments()


class TestRevocationWriteGuard:
    def test_normal_revocation_succeeds(self):
        db = _seed()
        db.add_assignment(2, 10)
        _revoke(db)
        assert (2, 10) not in db.assignments()

    def test_concurrent_target_promotion_is_rejected(self):
        db = _seed()
        db.add_assignment(2, 10)

        def promote_target(conn):
            conn.execute(
                "INSERT INTO admin_roles "
                "(id, name, permissions, created_at, created_by, position) "
                "VALUES (22, 'elevated', '{}', 0, 1, 60)"
            )
            conn.execute(
                "INSERT INTO admin_role_assignments VALUES (2, 22, 0, 1)"
            )

        db.after_hierarchy = promote_target
        with pytest.raises(HTTPException) as exc:
            _revoke(db)
        assert exc.value.status_code == 409
        assert (2, 10) in db.assignments()
