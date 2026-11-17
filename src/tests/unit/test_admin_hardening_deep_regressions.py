"""Regressions for deep admin authz hardening.

Covers three privilege-boundary gaps:

* a locked admin account must not be able to reuse an existing admin session,
* malformed (non-boolean) role payload values must never grant a scope, and
* the memoised admin id must be bound to the credential that produced it so a
  planted/stale ``request.state`` value cannot authenticate a caller.

Run with: pytest -m unit src/tests/unit/test_admin_hardening_deep_regressions.py
"""

import hashlib

import pytest
from starlette.requests import Request

from src.api.routes.admin import utils as admin_utils
from src.core import admin as admin_core
from src.core.admin import permissions as perm_core
from src.core.admin import request_roles
from src.core.admin.auth.session_mixin import SessionMixin


pytestmark = pytest.mark.unit


class _DB:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.all_calls = []
        self.one_calls = []

    def fetch_all(self, sql, params=()):
        self.all_calls.append((sql, tuple(params)))
        return self.rows

    def fetch_one(self, sql, params=()):
        self.one_calls.append((sql, tuple(params)))
        return None

    def execute(self, sql, params=()):
        return None


@pytest.fixture(autouse=True)
def _clear_roles():
    request_roles.clear_session_roles()
    yield
    request_roles.clear_session_roles()


# --- locked account session gap --------------------------------------------


def test_validate_session_sql_rejects_locked_accounts():
    db = _DB()
    mixin = SessionMixin()
    mixin._db = db

    assert mixin.validate_session("tok") is None
    sql = db.all_calls[0][0]
    assert "FROM admin_sessions s" in sql
    assert "s.token = ? AND s.expires_at > ?" in sql
    # The lock check must be part of the same query (no second round trip and
    # no window where a locked account's token is accepted).
    assert "auth_users" in sql
    assert "account_locked" in sql


def test_locked_account_session_is_not_cached_and_drops_stale_roles():
    db = _DB()
    mixin = SessionMixin()
    mixin._db = db

    # A previously validated value in this context must not survive a failed
    # (locked-account) revalidation.
    request_roles.remember_session_roles(db, 42, ['{"*": true}'])
    assert request_roles.cached_session_roles(db, 42) is not None

    assert mixin.validate_session("tok") is None
    assert request_roles.cached_session_roles(db, 42) is None


def test_unlocked_account_session_still_validates_and_caches_roles():
    db = _DB([{"admin_id": 42, "permissions": '{"admin.read": true}'}])
    mixin = SessionMixin()
    mixin._db = db

    assert mixin.validate_session("tok") == 42
    assert request_roles.cached_session_roles(db, 42) == ('{"admin.read": true}',)


# --- non-boolean grant values ----------------------------------------------


@pytest.mark.parametrize("value", ["false", "true", 1, 1.0, ["x"], {"y": True}])
def test_truthy_non_boolean_values_do_not_grant_cached_scope(value):
    db = _DB()
    payload = {"admin.read": value, "*": value, "admin.*": value}
    request_roles.remember_session_roles(db, 42, [payload])

    result = perm_core.check_permission(42, "admin.read", db)
    assert result.has_permission is False
    assert (
        perm_core.check_permission(42, "admin.security.ipblock", db).has_permission
        is False
    )


def test_truthy_non_boolean_values_do_not_grant_fresh_scope():
    class _RoleDB(_DB):
        def fetch_all(self, sql, params=()):
            self.all_calls.append((sql, tuple(params)))
            return [
                {
                    "id": 1,
                    "name": "bad",
                    "description": "",
                    "permissions": {"*": "false", "admin.read": 1},
                    "created_at": 0,
                    "created_by": 0,
                    "is_system": 0,
                }
            ]

    assert (
        perm_core.check_permission(42, "admin.read", _RoleDB()).has_permission is False
    )


# --- admin id cache poisoning ----------------------------------------------


def _request(token="tok123"):
    headers = [(b"authorization", f"Bearer {token}".encode())]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/search/reindex",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def test_plain_planted_admin_id_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )
    request = _request()
    request.state._pcs_verified_admin_id = 999  # planted by another component

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.is_self_test_request", lambda r: False)
        assert admin_utils.get_admin_from_token(request) == 7

    assert calls == ["tok123"]


def test_bound_cache_with_wrong_token_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )
    request = _request("real-token")
    wrong_hash = hashlib.sha256(b"other-token").hexdigest()
    request.state._pcs_verified_admin_id = (("bearer", wrong_hash), 999)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.is_self_test_request", lambda r: False)
        assert admin_utils.get_admin_from_token(request) == 7

    assert calls == ["real-token"]


def test_bound_cache_with_matching_token_reuses_validated_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )
    request = _request("real-token")
    right_hash = hashlib.sha256(b"real-token").hexdigest()
    request.state._pcs_verified_admin_id = (("bearer", right_hash), 42)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.is_self_test_request", lambda r: False)
        assert admin_utils.get_admin_from_token(request) == 42

    assert calls == []


def test_self_test_binding_cannot_authorise_ordinary_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )
    request = _request()
    request.state._pcs_verified_admin_id = (("selftest",), 42)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.is_self_test_request", lambda r: False)
        assert admin_utils.get_admin_from_token(request) == 7

    assert calls == ["tok123"]
