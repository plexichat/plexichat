"""An approval token must be bound to the exact target and requesting admin.

``require_approved_request`` used to match only on ``action_type``. The
``admin_approvals`` row already records ``target_id`` and ``requested_by``, but
the gate ignored both: an approval granted to purge/delete user X could be
consumed by any admin holding the execution scope to purge/delete a completely
different user Y, and any admin could ride on another admin's approval. That
defeats the "two admins reviewed this specific destructive action" guarantee.

These tests model the approval table with realistic SQL-predicate semantics so
they fail against the action-only gate and pass only when the consume is atomic
*and* pinned to the exact ``(target_id, requested_by)``.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.admin import utils as admin_utils
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users import lifecycle as lc
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.admin import read_cache
from src.core.admin import permissions as perm_core


pytestmark = [pytest.mark.security, pytest.mark.unit]

NOW = int(time.time() * 1000)
FUTURE = NOW + 10_000_000


class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


def _approval(**overrides):
    row = {
        "id": 7001,
        "requested_by": 200,
        "action_type": "users.delete",
        "target_id": 111,
        "status": "approved",
        "expires_at": FUTURE,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


class _ApprovalDB:
    """Mini model of admin_approvals that evaluates the gate's SQL predicates.

    The gate's combined SELECT binds ``(action_type, now_ms, target, target,
    actor, actor)``; the atomic consume UPDATE binds ``(now_ms, id, now_ms,
    target, target, actor, actor)``. A target/actor value of ``None`` means
    "no constraint" (the ``? IS NULL OR ...`` disjunct). The old action-only
    gate passes none of these, which is exactly what the pre-fix assertions
    expose.
    """

    def __init__(self, rows, admin_count=5):
        self.rows = rows
        self.admin_count = admin_count
        self.executed = []

    @staticmethod
    def _norm(sql):
        return " ".join(str(sql).lower().split())

    def _matches(self, row, action_type, now_ms, target_id, actor_id):
        if row["action_type"] != action_type or row["status"] != "approved":
            return False
        if row["expires_at"] is None or int(row["expires_at"]) <= int(now_ms):
            return False
        if target_id is not None and row.get("target_id") != target_id:
            return False
        if actor_id is not None and row.get("requested_by") != actor_id:
            return False
        return True

    def fetch_one(self, sql, params=()):
        assert "admin_approvals" in self._norm(sql)
        p = tuple(params or ())
        action_type, now_ms = p[0], p[1]
        target_id = p[2] if len(p) > 2 else None
        actor_id = p[4] if len(p) > 4 else None
        candidates = [
            r
            for r in self.rows
            if self._matches(r, action_type, now_ms, target_id, actor_id)
        ]
        candidates.sort(key=lambda r: r["updated_at"], reverse=True)
        return {
            "admin_count": self.admin_count,
            "approval_id": candidates[0]["id"] if candidates else None,
        }

    def execute(self, sql, params=()):
        assert "update admin_approvals" in self._norm(sql)
        p = tuple(params or ())
        self.executed.append((self._norm(sql), p))
        row_id = p[1]
        check_now = p[2] if len(p) > 2 else None
        target_id = p[3] if len(p) > 3 else None
        actor_id = p[5] if len(p) > 5 else None
        for r in self.rows:
            if r["id"] != row_id:
                continue
            if r["status"] != "approved":
                return _Cursor(0)
            if (
                check_now is not None
                and r["expires_at"] is not None
                and int(r["expires_at"]) <= int(check_now)
            ):
                return _Cursor(0)
            if target_id is not None and r.get("target_id") != target_id:
                return _Cursor(0)
            if actor_id is not None and r.get("requested_by") != actor_id:
                return _Cursor(0)
            r["status"] = "consumed"
            return _Cursor(1)
        return _Cursor(0)


def _call_gate(monkeypatch, action, context, rows, admin_count=5):
    """Drive the real gate, tolerating the pre-fix single-argument signature.

    Pre-fix the gate cannot accept a binding at all, so we fall back to the
    action-only call to demonstrate that the swap actually goes through; the
    security assertion then fails for the right (behavioural) reason.
    """
    db = _ApprovalDB(rows, admin_count)
    monkeypatch.setattr(api, "get_db", lambda: db)
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
    monkeypatch.setattr(read_cache, "invalidate_approval_caches", lambda: None)
    try:
        result = admin_utils.require_approved_request(action, context)
    except TypeError:
        # Pre-fix single-argument gate: demonstrate the action-only behaviour.
        try:
            result = admin_utils.require_approved_request(action)
        except HTTPException as exc:
            result = exc
    except HTTPException as exc:
        # Correct behaviour for a mismatched binding is a 403 refusal.
        result = exc
    return db, result


def _assert_refused(result, db):
    """The gate must refuse without ever issuing the consume write."""
    assert isinstance(result, HTTPException), (
        "a target/actor mismatch must be refused with 403, not authorized"
    )
    assert result.status_code == 403
    assert all("update admin_approvals" not in sql for sql, _ in db.executed), (
        "no approval may be consumed for a mismatched target/actor"
    )


class TestApprovalTargetBinding:
    def test_approval_for_target_x_cannot_authorize_target_y(self, monkeypatch):
        # Approval was granted for user 111; try to delete user 999 with it.
        db, result = _call_gate(
            monkeypatch,
            "users.delete",
            (999, 200),
            [_approval(target_id=111, requested_by=200)],
        )
        _assert_refused(result, db)

    def test_approval_cannot_be_executed_by_a_different_admin(self, monkeypatch):
        # Approval was requested by admin 200; admin 201 must not ride on it.
        db, result = _call_gate(
            monkeypatch,
            "users.delete",
            (111, 201),
            [_approval(target_id=111, requested_by=200)],
        )
        _assert_refused(result, db)

    def test_correct_target_and_admin_consumes_the_approval(self, monkeypatch):
        rows = [_approval(target_id=111, requested_by=200)]
        db, approval_id = _call_gate(monkeypatch, "users.delete", (111, 200), rows)
        assert approval_id == 7001
        assert len(db.executed) == 1
        assert rows[0]["status"] == "consumed"

    def test_consume_update_carries_the_full_binding(self, monkeypatch):
        db, _ = _call_gate(
            monkeypatch,
            "users.delete",
            (111, 200),
            [_approval(target_id=111, requested_by=200)],
        )
        sqls = [sql for sql, _ in db.executed if "update admin_approvals" in sql]
        assert len(sqls) == 1
        params = [p for sql, p in db.executed if "update admin_approvals" in sql][0]
        assert 111 in params, f"target id missing from consume params: {params}"
        assert 200 in params, f"requesting admin missing from consume params: {params}"
        assert "target_id" in sqls[0] and "requested_by" in sqls[0], (
            "consume UPDATE must re-check target and requester atomically"
        )

    def test_same_approval_is_single_use(self, monkeypatch):
        rows = [_approval(target_id=111, requested_by=200)]
        db, first = _call_gate(monkeypatch, "users.delete", (111, 200), rows)
        assert first == 7001
        assert rows[0]["status"] == "consumed"
        db2, second = _call_gate(monkeypatch, "users.delete", (111, 200), rows)
        _assert_refused(second, db2)


class _ExecDB:
    def execute(self, sql, params=()):
        return _Cursor(1)

    def fetch_one(self, sql, params=()):
        return {"target_exists": True, "current_pos": 100, "target_pos": 10}


class _AuthStub:
    def force_purge_account(self, uid, admin_id=None):
        return True


class TestCallSitesBindTarget:
    """The fix is only real if the destructive routes actually pass the binding."""

    def test_delete_route_passes_target_and_actor(self, monkeypatch):
        calls = []

        def _spy(action, **kwargs):
            calls.append((action, kwargs))
            return 4242

        monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: 100)
        monkeypatch.setattr(au, "invalidate_privilege_caches", lambda: None)
        monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)
        monkeypatch.setattr(
            au,
            "_load_admin_hierarchy",
            lambda *a: {"target_exists": True, "current_pos": 100, "target_pos": 10},
        )
        monkeypatch.setattr(au, "_can_manage_admin_at", lambda *a: True)
        monkeypatch.setattr(au, "_assert_hierarchy_at_write", lambda *a: None)
        monkeypatch.setattr(au, "require_approved_request", _spy)
        monkeypatch.setattr(api, "get_db", lambda: _ExecDB())

        class Router(au.AdminUsersMixin, AdminUsersRouterBase):
            pass

        asyncio.run(Router().delete_admin_user(SimpleNamespace(client=None), "50"))
        assert calls == [("users.delete", {"target_context": (50, 100)})]

    def test_force_purge_route_passes_target_and_actor(self, monkeypatch):
        calls = []

        def _spy(action, **kwargs):
            calls.append((action, kwargs))
            return 1

        monkeypatch.setattr(lc, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(lc, "require_admin_permission", lambda request, perm: 100)
        monkeypatch.setattr(
            lc,
            "_load_admin_hierarchy",
            lambda *a: {"target_exists": True, "current_pos": 100, "target_pos": 10},
        )
        monkeypatch.setattr(lc, "_can_manage_admin_at", lambda *a: True)
        monkeypatch.setattr(lc, "_assert_hierarchy_at_write", lambda *a: None)
        monkeypatch.setattr(lc, "require_approved_request", _spy)
        monkeypatch.setattr(api, "get_db", lambda: _ExecDB())
        monkeypatch.setattr(api, "get_auth", lambda: _AuthStub())

        class Router(lc.LifecycleMixin, AdminUsersRouterBase):
            pass

        asyncio.run(
            Router().admin_force_purge_account(SimpleNamespace(client=None), "50")
        )
        assert calls == [("users.force_purge", {"target_context": (50, 100)})]
