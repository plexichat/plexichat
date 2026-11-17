"""Focused regressions for approval-flow hardening in the admin API.

Covers two weaknesses prior waves missed:

* ``cancel_approval`` used a read-then-unconditional-UPDATE, so a concurrent
  state transition could be silently clobbered back to 'cancelled' (TOCTOU).
* ``request_approval`` copied a caller-controlled ``target_type`` verbatim into
  a single-line audit log message, allowing audit log-line forgery via CRLF.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam


def _build_request(path: str, method: str = "GET") -> Request:
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
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _FakeDB:
    """Records SQL and lets a test choose the UPDATE's rowcount."""

    def __init__(self, update_rowcount: int = 1):
        self.update_rowcount = update_rowcount
        self.executed = []

    def fetch_one(self, query, params=None):
        q = query.lower()
        if "from admin_approvals" in q and "count(*)" not in q:
            # The cancel route's advisory read: a pending request owned by 7.
            return {"id": 42, "status": "pending", "requested_by": 7, "expires_at": None}
        return {"count": 2}

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return _Cursor(self.update_rowcount)


class TestCancelApprovalCompareAndSwap:
    """A cancel must never overwrite a newer approval state (TOCTOU)."""

    @staticmethod
    def _run_cancel(db):
        from src.api.routes.admin import approvals as routes

        request = _build_request("/approvals/42", method="DELETE")
        # The caller is the requester, so the "requester or super admin" gate
        # passes without exercising check_permission.
        with (
            patch.object(routes, "check_host_restriction"),
            patch.object(routes, "require_admin_permission", return_value=7),
            patch("src.api.get_db", return_value=db),
            patch("src.core.admin.permissions.log_admin_action") as audit,
        ):
            result = asyncio.run(routes.cancel_approval(request, SnowflakeIDParam(42)))
        return result, audit

    def test_update_is_guarded_on_pending_status(self):
        db = _FakeDB(update_rowcount=1)
        self._run_cancel(db)
        cancel_sql = " ".join(q for q, _ in db.executed if "UPDATE admin_approvals" in q)
        assert "SET status = 'cancelled'" in cancel_sql
        # The write must be conditional on the status observed by the read.
        assert "status = 'pending'" in cancel_sql

    def test_stale_cancel_loses_the_race_and_returns_409(self):
        from src.api.routes.admin import approvals as routes

        request = _build_request("/approvals/42", method="DELETE")
        db = _FakeDB(update_rowcount=0)  # another vote/status change landed first
        with (
            patch.object(routes, "check_host_restriction"),
            patch.object(routes, "require_admin_permission", return_value=7),
            patch("src.api.get_db", return_value=db),
            patch("src.core.admin.permissions.log_admin_action") as audit,
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    routes.cancel_approval(request, SnowflakeIDParam(42))
                )
        assert exc.value.status_code == 409
        # A cancel that did not happen must not be recorded in the audit log.
        audit.assert_not_called()

    def test_legitimate_pending_cancel_still_succeeds(self):
        result, audit = self._run_cancel(_FakeDB(update_rowcount=1))
        assert result.success is True
        assert audit.call_count == 1
        assert audit.call_args.args[2] == "approval.cancel"


class TestApprovalAuditLogInjection:
    """Untrusted target_type cannot forge audit log lines."""

    def test_target_type_control_characters_are_stripped_for_the_audit_log(self):
        from src.api.routes.admin import approvals as routes
        from src.api.routes.admin.approvals import ApprovalRequest

        forged = "sv\r\nAdminID=1"
        body = ApprovalRequest(
            action_type="migrations.run",
            target_type=forged,
            action_details="details",
        )

        config = {
            "admin_ui": {
                "approval_workflows": {
                    "enabled": True,
                    "single_admin_bypass": False,
                }
            }
        }

        request = _build_request("/approvals/request", method="POST")
        with (
            patch.object(routes, "check_host_restriction"),
            patch.object(routes, "require_admin_permission", return_value=7),
            patch("src.api.get_db", return_value=_FakeDB()),
            patch("utils.config.get", return_value=config),
            patch(
                "src.utils.encryption.generate_snowflake_id", return_value=12345
            ),
            patch("src.utils.system_alerts.record_system_alert"),
            patch("src.core.admin.permissions.log_admin_action") as audit,
        ):
            asyncio.run(routes.request_approval(request, body))

        assert audit.call_count == 1
        logged_target_type = audit.call_args.args[3]
        # No C0/DEL control bytes survive, so the value cannot break out of its
        # single-line audit entry and forge a new log line.
        assert not any(ord(ch) <= 0x1F or ord(ch) == 0x7F for ch in logged_target_type)
