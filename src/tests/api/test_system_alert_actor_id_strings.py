"""System-alert details must carry snowflake ids as strings.

Several admin routes record a ``system_alert`` whose ``details`` JSON embeds
snowflake ids. The adjacent ids in the SAME dict were already stringified
(``user_id``, ``approval_id``, ``token_id`` in some spots) while the acting
admin's id (``*_by``) and the token id were left as JSON numbers. A JS
``Number`` cannot hold a 64-bit snowflake exactly, so any consumer that parses
the alert ``details`` would address the wrong admin/token once ids pass 2**53.

These tests patch the admin guards out (so the real alert call runs), stub the
downstream managers, capture the ``details`` handed to ``record_system_alert``,
and pin every snowflake-typed value to ``str``.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2**53: a JSON number would lose precision
BIG_ADMIN = BIG + 100
BIG_USER = BIG + 200
BIG_TOKEN = BIG + 300


class _AlertCapture:
    """Callable stand-in for ``record_system_alert`` that records calls."""

    def __init__(self):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def __call__(self, event_type, details=None, **kwargs):
        self.calls.append((event_type, dict(details or {})))


def _patch_guards(module):
    """Patch host restriction + admin permission in a route module."""
    return (
        patch.object(module, "check_host_restriction", return_value=None),
        patch.object(module, "require_admin_permission", return_value=BIG_ADMIN),
    )


def _assert_str_id(details: Dict[str, Any], *keys: str):
    for key in keys:
        assert key in details, f"missing {key!r} in {details!r}"
        assert isinstance(details[key], str), (
            f"{key!r} must be a decimal string, got {details[key]!r}"
        )


# ---------------------------------------------------------------------------
# security.py: token lifecycle + session actions
# ---------------------------------------------------------------------------


def _security_client():
    from src.api.routes.admin import security as security_routes

    app = FastAPI()
    app.include_router(security_routes.router, prefix="/api/v1/admin")
    return app, security_routes


def _fake_token():
    return SimpleNamespace(
        token="tok",
        id=BIG_TOKEN,
        name="t",
        description=None,
        created_by=BIG_ADMIN,
        created_at=1,
        first_used_at=None,
        last_used_at=None,
        last_used_ip_address=None,
        last_used_user_agent=None,
        last_used_path=None,
        expires_at=None,
        scope_mode="none",
        use_count_total=0,
        distinct_ip_count=0,
        denied_count_total=0,
        revoked=False,
        revoked_at=None,
        revoked_by=None,
    )


def test_token_revoked_alert_stringifies_ids():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.revoke_api_access_token", return_value=True),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/security/access-tokens/{BIG_TOKEN}/revoke"
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "revoke must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "token_id", "revoked_by")
    assert details["token_id"] == str(BIG_TOKEN)
    assert details["revoked_by"] == str(BIG_ADMIN)


def test_token_unrevoked_alert_stringifies_ids():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.unrevoke_api_access_token", return_value=True),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/security/access-tokens/{BIG_TOKEN}/unrevoke"
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "unrevoke must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "token_id", "unrevoked_by")
    assert details["token_id"] == str(BIG_TOKEN)
    assert details["unrevoked_by"] == str(BIG_ADMIN)


def test_token_rotated_alert_stringifies_ids():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.rotate_api_access_token", return_value=_fake_token()),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/security/access-tokens/{BIG_TOKEN}/rotate", json={}
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "rotate must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "token_id", "rotated_by")
    assert details["token_id"] == str(BIG_TOKEN)
    assert details["rotated_by"] == str(BIG_ADMIN)


def test_force_logout_alert_stringifies_actor():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.logout_all", return_value=None),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/admin/security/force-logout",
            json={"user_id": str(BIG_USER)},
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "force-logout must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "user_id", "initiated_by")
    assert details["initiated_by"] == str(BIG_ADMIN)


def test_lock_user_alert_stringifies_actor():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.admin.lock_user", return_value=None),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/admin/security/lock-user",
            json={"user_id": str(BIG_USER), "duration_seconds": 60},
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "lock-user must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "user_id", "suspended_by")
    assert details["suspended_by"] == str(BIG_ADMIN)


def test_logout_all_alert_stringifies_actor():
    app, routes = _security_client()
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.logout_all_users", return_value=None),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/admin/security/logout-all"
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "logout-all must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "initiated_by")
    assert details["initiated_by"] == str(BIG_ADMIN)


# ---------------------------------------------------------------------------
# approvals.py: request / approve / vote / reject
# ---------------------------------------------------------------------------


def _approvals_client(db):
    from src.api.routes.admin import approvals as approvals_routes

    app = FastAPI()
    app.include_router(approvals_routes.router, prefix="/api/v1/admin")
    return app, approvals_routes


def _seed_admins(db):
    now = 1760000000000
    for i, aid in enumerate((BIG_ADMIN, BIG_ADMIN + 1)):
        db.execute(
            "INSERT INTO admin_users (id, username, password_hash, created_at)"
            " VALUES (?, ?, ?, ?)",
            (aid, f"alert_admin_{i}", "!", now),
        )


def _insert_approval(db, approval_id, requested_by, required=1):
    import time

    now = int(time.time() * 1000)
    db.execute(
        "INSERT INTO admin_approvals (id, requested_by, action_type, status,"
        " required_approvals, current_approvals, expires_at, created_at,"
        " updated_at) VALUES (?, ?, ?, 'pending', ?, 0, ?, ?, ?)",
        (
            approval_id,
            requested_by,
            "users.force_purge",
            required,
            now + 3600000,
            now,
            now,
        ),
    )


def test_approval_request_alert_stringifies_actor(db):
    import src.api as api

    app, routes = _approvals_client(db)
    _seed_admins(db)
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch.object(api, "get_db", return_value=db),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/admin/approvals/request",
            json={"action_type": "users.force_purge", "target_type": "user"},
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "approval request must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "approval_id", "requested_by")
    assert details["requested_by"] == str(BIG_ADMIN)


def test_approval_approved_alert_stringifies_actor(db):
    import src.api as api

    app, routes = _approvals_client(db)
    _seed_admins(db)
    approval_id = BIG + 500
    _insert_approval(db, approval_id, BIG_ADMIN + 1, required=1)
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch.object(api, "get_db", return_value=db),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/approvals/{approval_id}/approve"
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "approval threshold must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "approval_id", "approved_by")
    assert details["approved_by"] == str(BIG_ADMIN)


def test_approval_voted_alert_stringifies_actor(db):
    import src.api as api

    app, routes = _approvals_client(db)
    _seed_admins(db)
    approval_id = BIG + 501
    _insert_approval(db, approval_id, BIG_ADMIN + 1, required=2)
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch.object(api, "get_db", return_value=db),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/approvals/{approval_id}/approve"
        )
    assert resp.status_code == 200, resp.text
    voted = [d for _, d in capture.calls if "voted_by" in d]
    assert voted, f"intermediate vote must alert, got {capture.calls!r}"
    _assert_str_id(voted[-1], "approval_id", "voted_by")
    assert voted[-1]["voted_by"] == str(BIG_ADMIN)


def test_approval_rejected_alert_stringifies_actor(db):
    import src.api as api

    app, routes = _approvals_client(db)
    _seed_admins(db)
    approval_id = BIG + 502
    _insert_approval(db, approval_id, BIG_ADMIN + 1, required=1)
    capture = _AlertCapture()
    guard_a, guard_b = _patch_guards(routes)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch.object(api, "get_db", return_value=db),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/approvals/{approval_id}/reject",
            json={"decision": "reject", "reason": "nope"},
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "rejection must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "approval_id", "rejected_by")
    assert details["rejected_by"] == str(BIG_ADMIN)


# ---------------------------------------------------------------------------
# users composer: tier change + force purge
# ---------------------------------------------------------------------------


def _users_client():
    from fastapi import APIRouter

    from src.api.routes.admin.users.composer import AdminUsersRouter

    handler = AdminUsersRouter()
    router = APIRouter()
    handler.register_routes(router)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    return app


def test_tier_changed_alert_stringifies_actor():
    app = _users_client()
    capture = _AlertCapture()
    import src.api.routes.admin.users.management as management

    guard_a, guard_b = _patch_guards(management)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.admin.update_user_tier", return_value=True),
    ):
        resp = TestClient(app, raise_server_exceptions=False).put(
            f"/api/v1/admin/users/{BIG_USER}/tier", json={"tier": "premium"}
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "tier change must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "user_id", "changed_by")
    assert details["changed_by"] == str(BIG_ADMIN)


def test_user_purged_alert_stringifies_actor():
    app = _users_client()
    capture = _AlertCapture()
    import src.api.routes.admin.users.lifecycle as lifecycle

    guard_a, guard_b = _patch_guards(lifecycle)
    with (
        guard_a,
        guard_b,
        patch("src.utils.system_alerts.record_system_alert", capture),
        patch("src.core.auth.force_purge_account", return_value=None),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/admin/users/{BIG_USER}/force-purge"
        )
    assert resp.status_code == 200, resp.text
    assert capture.calls, "force purge must record a system alert"
    _, details = capture.calls[-1]
    _assert_str_id(details, "user_id", "purged_by")
    assert details["purged_by"] == str(BIG_ADMIN)
