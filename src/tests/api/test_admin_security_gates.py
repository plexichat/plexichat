"""Tests for admin route permission scopes, approval gates, and self-test hardening."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi import FastAPI
from unittest.mock import patch
from starlette.requests import Request


def _build_request(path: str = "/audit/logs", method: str = "GET") -> Request:
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


class TestAuditRequiresPermission:
    """[C1] Audit log endpoints must require an authenticated, authorized admin."""

    def _app(self):
        from src.api.routes.admin.audit import router as audit_router

        app = FastAPI()
        app.include_router(audit_router, prefix="/api/v1/admin")
        return app

    def _assert_unauthenticated_rejected(self, path: str):
        """With host restriction neutralized, missing auth must still 401/403."""
        from src.api.routes.admin import audit as audit_routes

        client = TestClient(self._app())
        with patch.object(audit_routes, "check_host_restriction"):
            response = client.get(path)
        assert response.status_code == 401

    def test_audit_logs_reject_unauthenticated(self):
        self._assert_unauthenticated_rejected("/api/v1/admin/audit/logs")

    def test_audit_stats_reject_unauthenticated(self):
        self._assert_unauthenticated_rejected("/api/v1/admin/audit/logs/stats")

    def test_audit_export_reject_unauthenticated(self):
        self._assert_unauthenticated_rejected("/api/v1/admin/audit/logs/export")


class TestRequireApprovedRequest:
    """Approval gate helper semantics (approvals are strictly single-use)."""

    @staticmethod
    def _fake_db(admins=3, approved_row=None):
        class Cursor:
            rowcount = 1

        class FakeDB:
            def __init__(self):
                self.executed = []

            def fetch_one(self, query, params=None):
                q = query.lower()
                if "count(*)" in q:
                    return {"c": admins}
                return approved_row

            def execute(self, query, params=None):
                self.executed.append(query)
                return Cursor()

        return FakeDB()

    def test_blocks_without_approved_request(self):
        from src.api.routes.admin.utils import require_approved_request

        with patch("src.api.get_db", return_value=self._fake_db(approved_row=None)):
            with pytest.raises(HTTPException) as exc:
                require_approved_request("migrations.rollback")
        assert exc.value.status_code == 403

    def test_passes_with_approved_request(self):
        from src.api.routes.admin.utils import require_approved_request

        db = self._fake_db(approved_row={"id": 42})
        with patch("src.api.get_db", return_value=db):
            assert require_approved_request("migrations.run") == 42
        # SECURITY: the approval must be consumed, not left reusable.
        assert db.executed and "status = 'consumed'" in db.executed[0]

    def test_approval_is_single_use(self):
        """A second gated execution after consumption must be blocked."""
        from src.api.routes.admin.utils import require_approved_request

        class Cursor:
            rowcount = 1

        class ConsumedDB:
            def __init__(self):
                self.claimed = False

            def fetch_one(self, query, params=None):
                q = query.lower()
                if "count(*)" in q:
                    return {"c": 3}
                # After the claim UPDATE, nothing approved remains.
                if not self.claimed:
                    return {"id": 42}
                return None

            def execute(self, query, params=None):
                self.claimed = True
                return Cursor()

        db = ConsumedDB()
        with patch("src.api.get_db", return_value=db):
            assert require_approved_request("migrations.run") == 42

        class PostConsumptionDB(ConsumedDB):
            def fetch_one(self, query, params=None):
                q = query.lower()
                if "count(*)" in q:
                    return {"c": 3}
                return None  # approval already 'consumed'

        with patch("src.api.get_db", return_value=PostConsumptionDB()):
            with pytest.raises(HTTPException) as exc:
                require_approved_request("migrations.run")
        assert exc.value.status_code == 403

    def test_concurrent_claim_blocked_by_rowcount(self):
        """rowcount 0 on the consume UPDATE must deny, never authorize."""
        from src.api.routes.admin.utils import require_approved_request

        class Cursor:
            rowcount = 0

        class RacyDB:
            def fetch_one(self, query, params=None):
                q = query.lower()
                if "count(*)" in q:
                    return {"c": 3}
                return {"id": 7}

            def execute(self, query, params=None):
                return Cursor()

        with patch("src.api.get_db", return_value=RacyDB()):
            with pytest.raises(HTTPException) as exc:
                require_approved_request("artifacts.force_delete")
        assert exc.value.status_code == 403

    def test_single_admin_bypass_applies(self):
        from src.api.routes.admin.utils import require_approved_request

        with patch(
            "src.api.get_db",
            return_value=self._fake_db(admins=1, approved_row=None),
        ):
            assert require_approved_request("search.reindex") is None


class TestApprovalVoteIntegrity:
    """approved_by is a JSON list; membership checks must be exact."""

    def test_parse_approved_by_handles_all_storage_formats(self):
        from src.api.routes.admin.approvals import _parse_approved_by

        assert _parse_approved_by(None) == []
        assert _parse_approved_by("") == []
        assert _parse_approved_by(12) == [12]
        assert _parse_approved_by("12,45") == [12, 45]
        assert _parse_approved_by("[12, 45]") == [12, 45]

    def test_admin_12_is_not_admin_112(self):
        # The old substring check (str(12) in "112") misfired.
        from src.api.routes.admin.approvals import _parse_approved_by

        voters = _parse_approved_by(json.dumps([112]))
        assert 12 not in voters
        assert 112 in voters


class TestEmergencyOverrideSingleUse:
    """Emergency override tokens must be consumed on first validation."""

    @staticmethod
    def _tracker(db):
        from src.core.migrations.tracker import MigrationTracker

        tracker = MigrationTracker.__new__(MigrationTracker)
        tracker.db = db
        return tracker

    def test_first_validation_consumes_token(self):
        executed = []

        class Cursor:
            rowcount = 1

        class FakeDB:
            def execute(self, query, params=None):
                executed.append(query)
                return Cursor()

            def fetch_one(self, query, params=None):
                return None

        assert self._tracker(FakeDB())._validate_emergency_override("tok") is True
        assert executed and executed[0].strip().upper().startswith("UPDATE")

    def test_already_used_token_rejected(self):
        class Cursor:
            rowcount = 0

        class FakeDB:
            def execute(self, query, params=None):
                return Cursor()

            def fetch_one(self, query, params=None):
                return {"used_by": "someone-else"}

        assert self._tracker(FakeDB())._validate_emergency_override("tok") is False


class TestAccessTokenServerSideGeneration:
    """Caller-supplied token values must be rejected (M29)."""

    def test_create_rejects_client_token(self):
        from src.api.routes.admin import security as security_routes
        from src.api.schemas.admin import AccessTokenCreateRequest

        request = _build_request("/security/access-tokens", method="POST")
        body = AccessTokenCreateRequest(name="t", token="a" * 48)
        with (
            patch.object(security_routes, "check_host_restriction"),
            patch.object(security_routes, "require_admin_permission", return_value=1),
        ):
            with pytest.raises(HTTPException) as exc:
                __import__("asyncio").run(
                    security_routes.create_access_token(request, body)
                )
        assert exc.value.status_code == 400

    def test_rotate_rejects_client_token(self):
        from src.api.routes.admin import security as security_routes
        from src.api.schemas.admin import AccessTokenRotateRequest

        request = _build_request("/security/access-tokens/1/rotate", method="POST")
        body = AccessTokenRotateRequest(token="b" * 48)
        with (
            patch.object(security_routes, "check_host_restriction"),
            patch.object(security_routes, "require_admin_permission", return_value=1),
        ):
            with pytest.raises(HTTPException) as exc:
                __import__("asyncio").run(
                    security_routes.rotate_access_token(
                        request=request, token_id=1, body=body
                    )
                )
        assert exc.value.status_code == 400


class TestAdminHostRestriction:
    """CIDR allowlists must use real network membership, not string prefixes."""

    def test_cidr_does_not_match_other_subnets(self):
        from src.core import admin

        assert admin.check_host_restriction("192.168.1.42", ["192.168.1.0/24"])
        assert not admin.check_host_restriction("192.168.99.42", ["192.168.1.0/24"])
        assert not admin.check_host_restriction("192.168.evil", ["192.168.1.0/24"])


class TestRoleInputValidation:
    """Delegated roles cannot create wildcard or out-of-hierarchy access."""

    def test_role_create_rejects_subscope_wildcard(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleCreate

        request = _build_request("/roles", method="POST")
        body = RoleCreate(
            name="wild-subscope",
            description="bad",
            permissions={"admin.*": True},
        )
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                __import__("asyncio").run(role_routes.create_role(request, body))
        assert exc.value.status_code == 400

    def test_role_create_rejects_wildcard(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleCreate

        request = _build_request("/roles", method="POST")
        body = RoleCreate(
            name="wild",
            description="bad",
            permissions={"*": True},
        )
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                __import__("asyncio").run(role_routes.create_role(request, body))
        assert exc.value.status_code == 400


class TestAdminApprovalExpiry:
    """Expired approval requests cannot be voted or rejected."""

    def test_expired_approval_vote_rejected(self):
        from src.api.routes.admin import approvals as approval_routes

        class Cursor:
            rowcount = 1

        class ExpiredDB:
            def fetch_one(self, query, params=None):
                q = query.lower()
                if "admin_approvals" in q:
                    if "current_approvals" in q:
                        return {
                            "id": 1,
                            "requested_by": 2,
                            "status": "pending",
                            "current_approvals": 0,
                            "required_approvals": 2,
                            "approved_by": None,
                            "expires_at": 1,
                        }
                    return {"status": "pending"}
                return {"id": 1}

            def execute(self, query, params=None):
                return Cursor()

        request = _build_request("/approvals/1/approve", method="POST")
        with (
            patch.object(approval_routes, "check_host_restriction"),
            patch.object(approval_routes, "require_admin_permission", return_value=3),
            patch("src.api.get_db", return_value=ExpiredDB()),
        ):
            with pytest.raises(HTTPException) as exc:
                __import__("asyncio").run(approval_routes.approve_request(request, 1))
        assert exc.value.status_code == 400


class TestSelfTestHardening:
    """[S1] Self-test signals alone never grant admin access."""

    def test_self_test_without_state_user_does_not_skip_token_validation(self):
        from src.api.routes.admin.utils import get_admin_from_token

        request = _build_request()
        # Simulate a spoofed internal-secret header only: no state.user.
        with patch("src.api.is_self_test_request", return_value=True):
            with pytest.raises(HTTPException) as exc:
                get_admin_from_token(request)
        assert exc.value.status_code == 401

    def test_authenticated_self_test_requires_populated_user(self):
        from src.api.routes.admin.utils import is_authenticated_self_test

        request = _build_request()
        with patch("src.api.is_self_test_request", return_value=True):
            # No request.state.user populated -> not an authenticated self-test.
            assert is_authenticated_self_test(request) is False
