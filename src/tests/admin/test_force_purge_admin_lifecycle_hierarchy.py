"""Admin force-purge must honour the hierarchy and revoke admin sessions.

``POST /admin/users/{id}/force-purge`` only checked the ``users.force_purge``
scope. Unlike every other admin-targeting mutation (update/delete/toggle-status,
ban/unban admin, force-password-change) it never checked the role-position
hierarchy, so any admin holding that scope could immediately purge a superior
admin.  The purge cascade (``AccountReaper.purge_user``) deletes the target's
``admin_role_assignments`` (a demotion) and its ``auth_users`` row, but left the
rows in ``admin_sessions`` intact.  ``SessionMixin.validate_session`` only
invalidates a token when ``auth_users.account_locked = 1`` -- with the row gone,
that guard passes and the purged admin's bearer token keeps authenticating.

These tests pin both halves of the fix: the route refuses an upward purge, and
the reaper revokes the admin sessions of any account it purges.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.admin.users import lifecycle as lc
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.auth.deletion_log import DeletionLog
from src.core.auth.reaper import AccountReaper

pytestmark = [pytest.mark.security, pytest.mark.unit]


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


class _Auth:
    def __init__(self):
        self.purged = []

    def force_purge_account(self, uid, admin_id=None):
        self.purged.append((uid, admin_id))
        return True


class _Router(lc.LifecycleMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _patch(monkeypatch, db, auth, actor_id):
    monkeypatch.setattr(lc, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(lc, "require_admin_permission", lambda request, perm: actor_id)
    # Approval enforcement is exercised in its own test module; here we bypass
    # it so these tests stay focused on the hierarchy gate.
    monkeypatch.setattr(lc, "require_approved_request", lambda action, *a, **kw: None)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_auth", lambda: auth)


class TestForcePurgeHierarchyGate:
    def test_lower_admin_cannot_purge_superior_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=10, target_pos=100)
        auth = _Auth()
        _patch(monkeypatch, db, auth, actor_id=10)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_Router().admin_force_purge_account(_request(), "100"))

        assert exc.value.status_code == 403
        assert auth.purged == [], "superior admin was purged despite the hierarchy"

    def test_equal_position_cannot_purge_admin(self, monkeypatch):
        db = _HierarchyDB(current_pos=50, target_pos=50)
        auth = _Auth()
        _patch(monkeypatch, db, auth, actor_id=50)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_Router().admin_force_purge_account(_request(), "51"))

        assert exc.value.status_code == 403
        assert auth.purged == []

    def test_super_admin_can_still_purge_subordinate(self, monkeypatch):
        db = _HierarchyDB(current_pos=100, target_pos=10)
        auth = _Auth()
        _patch(monkeypatch, db, auth, actor_id=100)

        asyncio.run(_Router().admin_force_purge_account(_request(), "10"))

        assert auth.purged == [(10, 100)]


class _RecordingDB:
    """Minimal recording double for the reaper's cascade."""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor(1)

    def fetch_one(self, sql, params=()):
        return None

    def fetch_all(self, sql, params=()):
        return []

    def column_exists(self, table, column):
        return False


def test_purge_revokes_admin_sessions(tmp_path):
    db = _RecordingDB()
    reaper = AccountReaper(db, object())
    reaper._log = DeletionLog(file_path=str(tmp_path / "deletion.jsonl"))

    reaper.purge_user(4242, "purged-admin")

    assert any(
        sql.startswith("DELETE FROM admin_sessions WHERE admin_id")
        and params == (4242,)
        for sql, params in db.executed
    ), "purged admin's bearer token would remain valid"
