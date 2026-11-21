"""Force-purge must honour the approval workflow for dangerous actions.

``users.force_purge`` is a canonical ``APPROVAL_REQUIRED_ACTIONS`` entry and the
admin docs state that purging requires a second-admin approval. Every other
approval-required destructive route calls ``require_approved_request``; the
force-purge route was the lone exception, so a single admin holding the scope
could irreversibly erase an account (including a privileged one whose role
position they outrank) without the documented approval.

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
from src.api.routes.admin.users import lifecycle as lc
from src.api.routes.admin.users.base import AdminUsersRouterBase

pytestmark = [pytest.mark.security, pytest.mark.unit]

ACTOR_ID = 100
TARGET_ID = 50


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount

    def close(self):
        pass


class _ApprovalDB:
    """Serves hierarchy, approval-gate and approval-consume statements."""

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
        raise AssertionError(f"unexpected write: {normalized[:120]}")


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


def _patch(monkeypatch, db, auth):
    monkeypatch.setattr(lc, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(lc, "require_admin_permission", lambda request, perm: ACTOR_ID)
    # Pin the real gate: other admin test modules stub this symbol, and test
    # order must not decide whether these cases actually exercise it.
    monkeypatch.setattr(
        lc, "require_approved_request", admin_utils.require_approved_request
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_auth", lambda: auth)
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


def test_force_purge_without_approval_is_refused(monkeypatch):
    db = _ApprovalDB(admin_count=2, approval_id=None)
    auth = _Auth()
    _patch(monkeypatch, db, auth)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_Router().admin_force_purge_account(_request(), str(TARGET_ID)))

    assert exc.value.status_code == 403
    assert auth.purged == [], "account was purged without the required approval"


def test_force_purge_with_approved_request_proceeds_and_consumes_it(monkeypatch):
    db = _ApprovalDB(admin_count=2, approval_id=777)
    auth = _Auth()
    _patch(monkeypatch, db, auth)

    asyncio.run(_Router().admin_force_purge_account(_request(), str(TARGET_ID)))

    assert auth.purged == [(TARGET_ID, ACTOR_ID)]
    assert len(db.consumed) == 1, "the approved request must be consumed once"
    # The approval is consumed before any erasure side effect.
    approval_write = next(
        i for i, (sql, _) in enumerate(db.updates) if "update admin_approvals" in sql
    )
    write_sqls = [sql for sql, _ in db.updates]
    assert not any(
        "delete from" in sql or "update auth_users" in sql
        for sql in write_sqls[:approval_write]
    )


def test_single_admin_bypass_still_allows_force_purge(monkeypatch):
    db = _ApprovalDB(admin_count=1, approval_id=None)
    auth = _Auth()
    _patch(monkeypatch, db, auth)

    asyncio.run(_Router().admin_force_purge_account(_request(), str(TARGET_ID)))

    assert auth.purged == [(TARGET_ID, ACTOR_ID)]
    assert db.consumed == [], "single-admin bypass must not consume an approval"
