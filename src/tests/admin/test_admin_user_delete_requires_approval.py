"""Admin account deletion must honour the approval workflow.

``users.delete`` is a canonical ``APPROVAL_REQUIRED_ACTIONS`` entry and is in
the default ``require_approval_for`` list. ``delete_admin_user`` irreversibly
erases an admin's role assignments, sessions and auth user, but was the only
account-deletion route that never called ``require_approved_request`` (the
force-purge sibling was missing it too and is now gated). In a multi-admin
deployment a single holder of ``admin.edit`` could therefore delete another
admin account without the documented second-admin approval.

These tests exercise the real gate end-to-end: a controlled DB double reports
the admin count / approval state and the handler either erases or refuses.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
import src.api.routes.admin.utils as admin_utils
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.admin import permissions as perm_core

pytestmark = [pytest.mark.security, pytest.mark.unit]

ACTOR_ID = 100
TARGET_ID = 50


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount

    def close(self):
        pass


class _ApprovalDB:
    """Serves hierarchy, approval-gate and account-deletion statements."""

    def __init__(self, *, admin_count: int, approval_id):
        self.admin_count = admin_count
        self.approval_id = approval_id
        self.hierarchy = {
            "target_exists": True,
            "current_pos": 100,
            "target_pos": 10,
            "account_locked": 0,
        }
        self.consumed = []
        self.updates = []
        self.deletes = []

    def fetch_one(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        if "target_exists" in normalized:
            return dict(self.hierarchy)
        if "admin_approvals" in normalized:
            return {
                "admin_count": self.admin_count,
                "approval_id": self.approval_id,
            }
        raise AssertionError(f"unexpected SELECT: {normalized[:120]}")

    def execute(self, sql, params=(), **kwargs):
        normalized = " ".join(str(sql).lower().split())
        self.updates.append((normalized, tuple(params or ())))
        if "update admin_approvals" in normalized:
            self.consumed.append(tuple(params or ()))
            return _Cursor(1)
        if "update admin_users set id = id" in normalized:
            return _Cursor(1)
        if "delete from" in normalized:
            self.deletes.append((normalized, tuple(params or ())))
            return _Cursor(1)
        raise AssertionError(f"unexpected write: {normalized[:120]}")


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _patch(monkeypatch, db):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: ACTOR_ID)
    monkeypatch.setattr(au, "invalidate_privilege_caches", lambda: None)
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)
    monkeypatch.setattr(perm_core, "can_grant_existing_role", lambda *a, **kw: True)
    # Pin the real gate: other admin test modules stub this symbol, and test
    # order must not decide whether these cases actually exercise it.
    monkeypatch.setattr(
        au, "require_approved_request", admin_utils.require_approved_request
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    # Multi-admin deployment: approval workflows enabled, single-admin bypass
    # configured but inapplicable (admin_count > 1).
    monkeypatch.setattr(
        admin_utils.config,
        "get",
        lambda key, default=None: {
            "admin_ui": {
                "approval_workflows": {
                    "enabled": True,
                    "single_admin_bypass": True,
                }
            }
        }.get(key, default),
    )


def test_admin_user_delete_without_approval_is_refused(monkeypatch):
    db = _ApprovalDB(admin_count=2, approval_id=None)
    _patch(monkeypatch, db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_Router().delete_admin_user(_request(), str(TARGET_ID)))

    assert exc.value.status_code == 403
    assert db.deletes == [], "admin account was deleted without the required approval"


def test_admin_user_delete_with_approved_request_proceeds_and_consumes_it(monkeypatch):
    db = _ApprovalDB(admin_count=2, approval_id=777)
    _patch(monkeypatch, db)

    asyncio.run(_Router().delete_admin_user(_request(), str(TARGET_ID)))

    assert len(db.deletes) == 4, "all cascading account deletes must still run"
    assert len(db.consumed) == 1, "the approved request must be consumed once"
    # The approval is consumed before any erasure side effect.
    approval_write = next(
        i for i, (sql, _) in enumerate(db.updates) if "update admin_approvals" in sql
    )
    assert not any("delete from" in sql for sql, _ in db.updates[:approval_write])


def test_single_admin_bypass_still_allows_admin_user_delete(monkeypatch):
    db = _ApprovalDB(admin_count=1, approval_id=None)
    _patch(monkeypatch, db)

    asyncio.run(_Router().delete_admin_user(_request(), str(TARGET_ID)))

    assert len(db.deletes) == 4
    assert db.consumed == [], "single-admin bypass must not consume an approval"
