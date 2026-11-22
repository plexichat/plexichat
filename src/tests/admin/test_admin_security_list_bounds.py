"""Regression tests: bounded admin security listings.

Three privileged reads returned the *entire* contents of attacker-growable
tables in one response:

* ``GET /admin/security/blocked-ips`` -> ``auth.get_blocked_ips()``
* ``GET /admin/security/access-tokens`` -> ``auth.list_api_access_tokens()``
* ``GET /admin/security/banned-usernames`` -> ``admin.get_banned_usernames()``

Each holder of the matching scope can create rows without a server-side cap,
so an unbounded listing was a worker-memory/CPU amplification vector (the
blocked-IP path also decrypted one row per record). All three manager/core
helpers now accept a clamped page, and the routes expose bounded pagination.
"""

import inspect
from types import SimpleNamespace

from fastapi.routing import APIRoute

from src.api.routes.admin import security as routes
from src.core.admin import users as admin_users
from src.core.auth import ip_blacklist as ip_api
from src.core.auth import tokens as token_api
from src.core.auth.managers import api_token as api_token_mgr
from src.core.auth.managers import ip_blacklist as ip_blacklist_mgr


class _RecordingDB:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return list(self._rows)


def _route(name):
    return next(
        r for r in routes.router.routes if isinstance(r, APIRoute) and r.name == name
    )


def _query_param(route_obj, name):
    return next(p for p in route_obj.dependant.query_params if p.name == name)


def _bound(model_field, attr):
    """Read a Query constraint from the field's metadata (Pydantic v2 style)."""
    for constraint in getattr(model_field.field_info, "metadata", ()):
        value = getattr(constraint, attr, None)
        if value is not None:
            return value
    # Older Pydantic versions keep the constraint on the field_info itself.
    return getattr(model_field.field_info, attr, None)


def _fake_manager(db):
    """Only ``_db`` and ``_get_timestamp`` are used by the two listing methods."""
    return SimpleNamespace(_db=db, _get_timestamp=lambda: 1000)


class TestBoundedSecurityListings:
    def test_routes_expose_bounded_pagination(self):
        for name in ("get_blocked_ips", "list_access_tokens", "get_banned_usernames"):
            route_obj = _route(name)
            limit = _query_param(route_obj, "limit")
            offset = _query_param(route_obj, "offset")
            assert _bound(limit, "ge") == 1, name
            assert _bound(limit, "le") == routes._MAX_SECURITY_LIST_LIMIT, name
            assert _bound(offset, "ge") == 0, name
            assert _bound(offset, "le") == routes._MAX_SECURITY_LIST_OFFSET, name

    def test_manager_helpers_have_a_page_signature(self):
        for fn in (
            ip_blacklist_mgr.IpBlacklistMixin.get_blocked_ips,
            api_token_mgr.ApiTokenMixin.list_api_access_tokens,
            ip_api.get_blocked_ips,
            token_api.list_api_access_tokens,
            admin_users.get_banned_usernames,
        ):
            params = inspect.signature(fn).parameters
            assert "limit" in params and "offset" in params, fn

    def test_blocked_ip_query_is_bounded_and_clamped(self):
        db = _RecordingDB()
        ip_blacklist_mgr.IpBlacklistMixin.get_blocked_ips(
            _fake_manager(db), limit=10**9, offset=-5
        )
        query, params = db.calls[-1]
        assert "LIMIT ? OFFSET ?" in query
        assert params == (500, 0)

    def test_access_token_query_is_bounded_and_clamped(self):
        db = _RecordingDB()
        api_token_mgr.ApiTokenMixin.list_api_access_tokens(
            _fake_manager(db), include_revoked=True, limit=10**9, offset=-3
        )
        query, params = db.calls[-1]
        assert "LIMIT ? OFFSET ?" in query
        assert params[-2:] == (500, 0)

    def test_banned_username_query_is_bounded_and_clamped(self):
        db = _RecordingDB()
        admin_users.get_banned_usernames(db, limit=10**9, offset=-4)
        query, params = db.calls[-1]
        assert "LIMIT ? OFFSET ?" in query
        assert params == (500, 0)
