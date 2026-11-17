"""Regressions for admin role TOCTOU and unbounded approval-comment pages.

* ``update_role``/``delete_role`` checked the role hierarchy in a SELECT and
  then wrote in a separate, unguarded statement. A concurrent admin could
  promote the role to a system role (or raise its position) in that window and
  the stale, previously-authorized snapshot would still modify/delete a now
  protected role (TOCTOU).
* ``get_approval_comments`` fetched an entire, attacker-growable comment thread
  with no LIMIT, so any ``admin.approvals`` holder could exhaust worker memory
  and stall the process by reading a large thread.
"""

import asyncio
import inspect
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam


def _request(path="/roles/1", method="POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class _Cursor:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _RoleDB:
    """Records writes and reports a controllable affected-row count."""

    def __init__(self, rowcount: int = 1, assignment_count: int = 0):
        self.rowcount = rowcount
        self.assignment_count = assignment_count
        self.executed = []

    def fetch_one(self, query, params=None):
        return {"count": self.assignment_count}

    def fetch_all(self, query, params=None):
        return []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return _Cursor(self.rowcount)


def _hierarchy() -> dict:
    return {
        "current_pos": 50,
        "target_pos": 10,
        "admin_exists": True,
        "role_pos": 5,
        "role_is_system": False,
        "role_permissions": json.dumps({"users.read": True}),
    }


def _run_update(db):
    from src.api.routes.admin import roles as routes
    from src.api.routes.admin.roles import RoleUpdate

    with ExitStack() as stack:
        stack.enter_context(patch.object(routes, "check_host_restriction"))
        stack.enter_context(patch.object(routes, "_require_role_manager", return_value=1))
        stack.enter_context(patch.object(routes, "_load_hierarchy", return_value=_hierarchy()))
        stack.enter_context(patch("src.api.get_db", return_value=db))
        invalidate = stack.enter_context(
            patch.object(routes, "invalidate_privilege_caches")
        )
        audit = stack.enter_context(
            patch("src.core.admin.permissions.log_admin_action")
        )
        exc = None
        try:
            asyncio.run(
                routes.update_role(
                    _request(method="PUT"),
                    SnowflakeIDParam(1),
                    RoleUpdate(description="x"),
                )
            )
        except HTTPException as caught:
            exc = caught
    return exc, invalidate, audit


def _run_delete(db):
    from src.api.routes.admin import roles as routes

    with ExitStack() as stack:
        stack.enter_context(patch.object(routes, "check_host_restriction"))
        stack.enter_context(patch.object(routes, "_require_role_manager", return_value=1))
        stack.enter_context(patch.object(routes, "_load_hierarchy", return_value=_hierarchy()))
        stack.enter_context(patch("src.api.get_db", return_value=db))
        invalidate = stack.enter_context(
            patch.object(routes, "invalidate_privilege_caches")
        )
        audit = stack.enter_context(
            patch("src.core.admin.permissions.log_admin_action")
        )
        exc = None
        try:
            asyncio.run(
                routes.delete_role(_request(method="DELETE"), SnowflakeIDParam(1))
            )
        except HTTPException as caught:
            exc = caught
    return exc, invalidate, audit


class TestRoleMutationCompareAndSwap:
    def test_update_reasserts_hierarchy_invariants_in_update(self):
        db = _RoleDB(rowcount=1)
        exc, _, _ = _run_update(db)
        assert exc is None

        update = next((q, p) for q, p in db.executed if q.startswith("UPDATE admin_roles"))
        sql, params = update
        assert "is_system = 0" in sql
        assert "position < ?" in sql
        # The actor's observed position is the guard bound at write time.
        assert 50 in params

    def test_update_losing_the_race_is_409_without_side_effects(self):
        db = _RoleDB(rowcount=0)  # role was promoted/changed concurrently
        exc, invalidate, audit = _run_update(db)
        assert exc is not None and exc.status_code == 409
        invalidate.assert_not_called()
        audit.assert_not_called()

    def test_delete_reasserts_hierarchy_invariants_in_delete(self):
        db = _RoleDB(rowcount=1, assignment_count=0)
        exc, _, _ = _run_delete(db)
        assert exc is None

        delete = next((q, p) for q, p in db.executed if q.startswith("DELETE FROM admin_roles"))
        sql, params = delete
        assert "is_system = 0" in sql
        assert "position < ?" in sql
        assert params == (1, 50)

    def test_delete_losing_the_race_is_409_without_side_effects(self):
        db = _RoleDB(rowcount=0, assignment_count=0)
        exc, invalidate, audit = _run_delete(db)
        assert exc is not None and exc.status_code == 409
        invalidate.assert_not_called()
        audit.assert_not_called()


class _CommentsDB:
    def __init__(self):
        self.queries = []

    def fetch_all(self, query, params=None):
        self.queries.append((query, params))
        return []

    def fetch_one(self, query, params=None):
        return {"id": 1}


class TestApprovalCommentsBounds:
    def test_comment_query_is_bounded(self):
        from src.api.routes.admin import approvals as routes

        db = _CommentsDB()
        with (
            patch.object(routes, "check_host_restriction"),
            patch.object(routes, "require_admin_permission", return_value=1),
            patch("src.api.get_db", return_value=db),
            patch("src.core.admin.permissions.log_admin_action"),
        ):
            asyncio.run(
                routes.get_approval_comments(
                    _request(path="/approvals/1/comments"),
                    SnowflakeIDParam(1),
                    limit=200,
                    offset=0,
                )
            )

        query, params = db.queries[0]
        assert "LIMIT ? OFFSET ?" in query
        assert params == (1, 200, 0)

    def test_comment_page_params_are_bounded(self):
        from src.api.routes.admin import approvals as routes

        sig = inspect.signature(routes.get_approval_comments)
        for name, expected_le in (("limit", 500), ("offset", 1_000_000)):
            default = sig.parameters[name].default
            le_values = [
                getattr(c, "le")
                for c in (getattr(default, "metadata", None) or [])
                if hasattr(c, "le")
            ]
            if hasattr(default, "le"):
                le_values.append(default.le)
            assert expected_le in le_values, (name, le_values)
