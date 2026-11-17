"""Query-count regressions for the shared admin auth preamble.

Every admin read endpoint used to pay two database round trips before reaching
its handler: one to validate the bearer token, a second to fetch the caller's
roles for the scope check. ``SessionMixin.validate_session`` now selects the
role permission payloads in the session query and caches them for the request;
``check_permission`` reuses them. These tests pin the resulting query counts
and prove the access decisions are unchanged (including the self-test path,
which never validates a session and must keep the old query).

Run with: pytest -m unit src/tests/unit/test_admin_session_role_batching.py
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam
import src.api as api
from src.api.routes.admin import approvals as approvals_routes
from src.api.routes.admin import audit as audit_routes
from src.api.routes.admin import roles as roles_routes
from src.api.routes.admin import utils as admin_utils
from src.api.routes.admin.users import admin_users as admin_users_routes
from src.core import admin as admin_core
from src.core.admin import permissions as perm_core
from src.core.admin import request_roles
from src.core.admin.auth.session_mixin import SessionMixin


pytestmark = pytest.mark.unit


class _CountingDB:
    """DB double that records reads and returns scripted rows by SQL fragment."""

    def __init__(self, responses):
        # responses: list of (sql fragment, fetch_all rows, fetch_one row)
        self.responses = responses
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append(("fetch_all", " ".join(sql.split()), tuple(params)))
        for fragment, rows, _ in self.responses:
            if fragment in sql:
                return rows
        return []

    def fetch_one(self, sql, params=()):
        self.calls.append(("fetch_one", " ".join(sql.split()), tuple(params)))
        for fragment, _, row in self.responses:
            if fragment in sql:
                return row
        return None


def _session_db(permission_payload, *, tuple_rows=False, rows_override=None):
    if rows_override is None:
        row = (
            (42, permission_payload)
            if tuple_rows
            else {"admin_id": 42, "permissions": permission_payload}
        )
        rows_override = [row]
    return _CountingDB([("admin_sessions s", rows_override, None)])


def _validate(db, token="tok"):
    mixin = SessionMixin()
    mixin._db = db
    request_roles.clear_session_roles()
    admin_id = mixin.validate_session(token)
    return admin_id


def _request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/admin-users",
            "headers": [(b"authorization", b"Bearer tok")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def _clear_roles():
    request_roles.clear_session_roles()
    yield
    request_roles.clear_session_roles()


# --- session validation ----------------------------------------------------


def test_validate_session_uses_one_query_and_caches_role_payloads():
    payload = '{"admin.read": true, "admin.roles": true}'
    db = _session_db(payload)

    assert _validate(db) == 42
    assert len(db.calls) == 1
    kind, sql, params = db.calls[0]
    assert kind == "fetch_all"
    assert "FROM admin_sessions s" in sql
    assert "LEFT JOIN admin_role_assignments" in sql
    assert "LEFT JOIN admin_roles" in sql
    assert "s.token = ? AND s.expires_at > ?" in sql
    assert params[0] and params[1]  # hashed token + expiry
    assert request_roles.cached_session_roles(db, 42) == (payload,)


def test_validate_session_handles_tuple_rows_and_multiple_roles():
    db = _session_db(
        None,
        rows_override=[
            (42, '{"admin.read": true}'),
            (42, '{"users.read": true}'),
        ],
    )
    assert _validate(db) == 42
    assert request_roles.cached_session_roles(db, 42) == (
        '{"admin.read": true}',
        '{"users.read": true}',
    )


def test_invalid_session_has_no_cached_roles():
    db = _session_db(None, rows_override=[])
    assert _validate(db) is None
    assert len(db.calls) == 1
    assert request_roles.cached_session_roles(db, 42) is None


def test_admin_without_roles_still_validates_but_grants_nothing():
    # LEFT JOIN yields one row with NULL permissions for a role-less admin.
    db = _session_db(None, rows_override=[{"admin_id": 42, "permissions": None}])
    assert _validate(db) == 42
    assert perm_core.check_permission(42, "admin.read", db).has_permission is False
    assert db.calls[-1][1].startswith("SELECT s.admin_id")


# --- permission gate reuse -------------------------------------------------


@pytest.mark.parametrize(
    "payload,scope,expected",
    [
        ('{"admin.read": true}', "admin.read", True),
        ('{"*": true}', "admin.read", True),
        ('{"admin.*": true}', "admin.read", True),
        ('{"admin.edit": true}', "admin.read", False),
        ("not json", "admin.read", False),
        (None, "admin.read", False),
    ],
)
def test_permission_check_uses_cached_roles_without_a_query(payload, scope, expected):
    db = _session_db(payload)
    _validate(db)
    db.calls.clear()

    result = perm_core.check_permission(42, scope, db)
    assert result.has_permission is expected
    assert db.calls == [], "permission gate must not re-query cached roles"


def test_super_admin_wildcard_across_multiple_roles_is_still_honoured():
    db = _session_db(
        None,
        rows_override=[
            {"admin_id": 42, "permissions": '{"users.read": true}'},
            {"admin_id": 42, "permissions": '{"admin.read": true}'},
        ],
    )
    _validate(db)
    db.calls.clear()
    assert perm_core.check_permission(42, "admin.read", db).has_permission is True
    assert db.calls == []


def test_permission_check_falls_back_to_one_query_without_cached_session():
    """The self-test identity path never validates a session, so it still queries."""
    role_row = {
        "id": 1,
        "name": "admin",
        "description": "",
        "permissions": '{"admin.read": true}',
        "created_at": 1,
        "created_by": 1,
        "is_system": 1,
    }
    db = _CountingDB([("FROM admin_roles r", [role_row], None)])
    assert perm_core.check_permission(42, "admin.read", db).has_permission is True
    assert len(db.calls) == 1
    assert "FROM admin_roles r" in db.calls[0][1]


def test_cached_roles_are_not_reused_for_a_different_admin_or_db():
    db = _session_db('{"admin.read": true}')
    other_db = _session_db('{"admin.read": true}')
    _validate(db)

    assert perm_core.check_permission(999, "admin.read", db).has_permission is False
    assert perm_core.check_permission(42, "admin.read", other_db).has_permission is False
    # The fallback paths each issue exactly one role query.
    assert len(db.calls) == 2
    assert len(other_db.calls) == 1


# --- route-level auth preamble ---------------------------------------------


def _patch_auth(monkeypatch, db):
    """Wire the real auth path to the counting DB, bypassing only host checks."""
    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: _validate(db, token),
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(admin_utils, "is_authenticated_self_test", lambda request: False)
    for module in (admin_users_routes, approvals_routes, audit_routes, roles_routes):
        monkeypatch.setattr(module, "check_host_restriction", lambda request: None)


def test_two_permission_gates_on_one_request_share_a_single_session_query(monkeypatch):
    db = _session_db('{"admin.read": true, "admin.roles": true}')
    _patch_auth(monkeypatch, db)
    request = _request()

    assert admin_utils.require_admin_permission(request, "admin.read") == 42
    assert len(db.calls) == 1
    assert admin_utils.require_admin_permission(request, "admin.roles") == 42
    # Admin id and roles are both cached on the request; no further reads.
    assert len(db.calls) == 1


# --- target endpoint read paths --------------------------------------------


def test_target_read_paths_issue_auth_plus_one_query(monkeypatch):
    """Each pinned endpoint costs one combined auth read + its own data read."""
    payload = (
        '{"admin.read": true, "admin.roles": true, '
        '"admin.approvals": true, "admin.audit.read": true}'
    )

    def make_db(data_fragment, data_rows):
        db = _session_db(payload)
        # Endpoint data rows are returned for the matching table fragment.
        db.responses.append((data_fragment, data_rows, data_rows[0] if data_rows else None))
        return db

    admin_row = {
        "id": 42,
        "username": "root",
        "email": "root@example.com",
        "role": "admin",
        "created_at": 1,
        "last_login_at": None,
        "account_locked": 0,
    }
    approval_row = {
        "id": 7,
        "requested_by": 42,
        "action_type": "migrations.run",
        "target_type": "system",
        "target_id": None,
        "action_details": None,
        "status": "pending",
        "required_approvals": 2,
        "current_approvals": 0,
        "approved_by": None,
        "rejected_by": None,
        "rejection_reason": None,
        "expires_at": None,
        "created_at": 1,
        "updated_at": 1,
    }

    cases = [
        (
            "get_admin_user",
            make_db("FROM admin_users a", [admin_row]),
            lambda: asyncio.run(
                admin_users_routes.AdminUsersMixin.get_admin_user(
                    _mixin(), _request(), "42"
                )
            ),
        ),
        (
            "list_admin_users",
            make_db("FROM admin_users a", [admin_row]),
            lambda: asyncio.run(
                admin_users_routes.AdminUsersMixin.list_admin_users(
                    _mixin(), _request()
                )
            ),
        ),
        (
            "get_admin_roles",
            make_db("FROM admin_role_assignments ra", []),
            lambda: asyncio.run(roles_routes.get_admin_roles(_request(), SnowflakeIDParam(42))),
        ),
        (
            "get_approval",
            make_db("FROM admin_approvals", [approval_row]),
            lambda: asyncio.run(approvals_routes.get_approval(_request(), SnowflakeIDParam(7))),
        ),
        (
            "export_audit_logs",
            make_db(
                "FROM admin_audit_log",
                [
                    {
                        "id": 1,
                        "admin_id": 42,
                        "action": "admin.read",
                        "target_type": "system",
                        "target_id": None,
                        "details": None,
                        "ip_address": None,
                        "user_agent": None,
                        "status": "success",
                        "created_at": 1,
                    }
                ],
            ),
            lambda: asyncio.run(audit_routes.export_audit_logs(_request())),
        ),
    ]

    for label, db, call in cases:
        _patch_auth(monkeypatch, db)
        request_roles.clear_session_roles()
        call()
        assert len(db.calls) == 2, (
            f"{label} issued {len(db.calls)} queries: {[c[1] for c in db.calls]}"
        )
        assert "admin_sessions s" in db.calls[0][1]


def test_combined_query_runs_against_a_real_database(db):
    """The batched SQL is valid on the real driver and preserves the decision."""
    import time

    from src.core.admin.auth.helpers import _hash_admin_token
    from src.core.admin.schema import create_admin_tables

    request_roles.clear_session_roles()
    create_admin_tables(db)
    token = "integration-token"
    now = int(time.time())
    db.execute(
        "INSERT INTO admin_users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (42, "root", "hash", now),
    )
    db.execute(
        "INSERT INTO admin_roles (id, name, permissions, created_at, created_by, is_system, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "admin", '{"admin.read": true}', now, 42, 1, 100),
    )
    db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) "
        "VALUES (?, ?, ?, ?)",
        (42, 1, now, 42),
    )
    db.execute(
        "INSERT INTO admin_sessions (id, admin_id, token, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, 42, _hash_admin_token(token), now, now + 3600),
    )

    mixin = SessionMixin()
    mixin._db = db
    assert mixin.validate_session(token) == 42
    before = db.get_request_metrics()["query_count"]
    assert perm_core.check_permission(42, "admin.read", db).has_permission is True
    assert db.get_request_metrics()["query_count"] == before


def _mixin():
    """Minimal admin-users route object backed by the patched api.get_db()."""

    class _Router(admin_users_routes.AdminUsersMixin):
        def _get_db(self):
            return api.get_db()

        def _get_auth(self):
            return object()

        def _parse_user_id(self, user_id):
            return int(user_id)

    return _Router()
