"""Locking an admin account must honour the role-position hierarchy.

``POST /admin/security/lock-user`` only checked the ``admin.security.sessions``
scope. Locking sets ``auth_users.account_locked = 1`` and logs out every session,
so it disables the target account. Every other admin-targeting mutation
(update/delete/toggle-status, ban/unban admin, force-password-change, force-purge)
refuses a target at or above the actor's role position, and the RBAC docs state
that an admin's status may only be changed for a lower-position admin. This route
had no position check at all, so any holder of the scope could disable a superior
admin -- or lock their own account -- bypassing the hierarchy.

These tests pin the gate: an upward/equal/self admin lock is refused before
``admin.lock_user`` runs, while locking a subordinate admin or an ordinary user
still works.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
import src.core.admin as admin_core
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


class _AdminCore:
    def __init__(self):
        self.locked = []

    def lock_user(self, uid, duration_seconds=None):
        self.locked.append((uid, duration_seconds))
        return True


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _body(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, duration_seconds=None)


def _patch(monkeypatch, db, core, actor_id):
    monkeypatch.setattr(security, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        security, "require_admin_permission", lambda request, perm: actor_id
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(admin_core, "lock_user", core.lock_user)


class TestLockUserHierarchyGate:
    def test_lower_admin_cannot_lock_superior_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=100)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_lock_user(_request(), _body(TARGET_ID)))

        assert exc.value.status_code == 403
        assert core.locked == [], "superior admin was disabled despite the hierarchy"

    def test_equal_position_cannot_lock_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=50, target_pos=50)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=50)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_lock_user(_request(), _body(51)))

        assert exc.value.status_code == 403
        assert core.locked == []

    def test_admin_cannot_lock_itself(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=10)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_lock_user(_request(), _body(ACTOR_ID)))

        assert exc.value.status_code == 403
        assert core.locked == [], "admin locked its own account via security route"

    def test_super_admin_can_still_lock_subordinate_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=100, target_pos=10)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=100)

        asyncio.run(security.admin_lock_user(_request(), _body(TARGET_ID)))

        assert core.locked == [(TARGET_ID, None)]

    def test_ordinary_user_target_is_not_hierarchy_gated(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=0, target_exists=False)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        asyncio.run(security.admin_lock_user(_request(), _body(TARGET_ID)))

        assert core.locked == [(TARGET_ID, None)]
