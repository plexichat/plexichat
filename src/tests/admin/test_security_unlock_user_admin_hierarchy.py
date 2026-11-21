"""Unlocking an admin account must honour the role-position hierarchy.

``POST /admin/security/unlock-user`` only checked the ``admin.security.sessions``
scope. Unlocking clears ``auth_users.account_locked`` and is the exact inverse of
``lock-user``: every other admin-targeting mutation (lock/update/delete/
toggle-status, ban/unban, force-password-change, force-purge) refuses a target at
or above the actor's role position. Without a gate here, any holder of the scope
could re-enable a superior admin that a higher-position admin had disabled, or
unlock its own account, bypassing the hierarchy.

These tests pin the gate: an upward/equal/self admin unlock is refused before
``admin.unlock_user`` runs (and the write-time hierarchy re-assert is issued for
allowed admin targets), while unlocking a subordinate admin or an ordinary user
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
            "account_locked": 1,
        }
        self.executed = []

    def fetch_one(self, sql, params=()):
        return self.row

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor(1)


class _AdminCore:
    def __init__(self):
        self.unlocked = []

    def unlock_user(self, uid):
        self.unlocked.append(uid)
        return True


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
    monkeypatch.setattr(admin_core, "unlock_user", core.unlock_user)


def _guard_writes(db):
    """Writes that re-assert the hierarchy at write time."""
    return [sql for sql, _ in db.executed if "SET id = id" in sql]


class TestUnlockUserHierarchyGate:
    def test_lower_admin_cannot_unlock_superior_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=100)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_unlock_user(_request(), _body(TARGET_ID)))

        assert exc.value.status_code == 403
        assert core.unlocked == [], "superior admin was re-enabled despite hierarchy"
        assert _guard_writes(db) == []

    def test_equal_position_cannot_unlock_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=50, target_pos=50)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=50)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_unlock_user(_request(), _body(51)))

        assert exc.value.status_code == 403
        assert core.unlocked == []

    def test_admin_cannot_unlock_itself(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=10)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(security.admin_unlock_user(_request(), _body(ACTOR_ID)))

        assert exc.value.status_code == 403
        assert core.unlocked == [], "admin unlocked its own account via security route"

    def test_super_admin_can_still_unlock_subordinate_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=100, target_pos=10)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=100)
        subordinate_id = 42

        asyncio.run(security.admin_unlock_user(_request(), _body(subordinate_id)))

        assert core.unlocked == [subordinate_id]
        assert len(_guard_writes(db)) == 1, "write-time hierarchy re-assert missing"

    def test_ordinary_user_target_is_not_hierarchy_gated(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=0, target_exists=False)
        core = _AdminCore()
        _patch(monkeypatch, db, core, actor_id=ACTOR_ID)

        asyncio.run(security.admin_unlock_user(_request(), _body(TARGET_ID)))

        assert core.unlocked == [TARGET_ID]
        assert _guard_writes(db) == []
