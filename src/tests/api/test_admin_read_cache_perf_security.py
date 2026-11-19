"""Regression tests for the cached admin read endpoints.

Covers ``GET /admin/approvals``, ``GET /admin/roles`` and
``GET /admin/admins/{id}/roles``:

* identical output — the cached bytes are byte-for-byte what the uncached
  handler produced (snowflake ids still stringified);
* performance — a warm call skips the backing query and the serialization
  work (asserted by query counts, not wall-clock time);
* security — host/scope gates run before the cache is consulted, the cache is
  scoped to the backing database, mutations invalidate it, and every list is
  bounded so one poll cannot render an unbounded response.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes.admin import approvals as approvals_route
from src.api.routes.admin import roles as roles_route
from src.core.admin import read_cache

BIG = 9007199254740993  # past 2^53


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _DB:
    """Counting fake DB; fetch_all returns whatever rows it was given."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.fetch_calls = 0

    def fetch_all(self, query, params=None):
        self.fetch_calls += 1
        return list(self.rows)

    def fetch_one(self, query, params=None):
        return None

    def execute(self, query, params=None):
        class _Cur:
            rowcount = 1

        return _Cur()


def _approval_row():
    return {
        "id": BIG,
        "requested_by": BIG + 1,
        "action_type": "server.delete",
        "target_type": "server",
        "target_id": BIG + 3,
        "action_details": None,
        "status": "pending",
        "required_approvals": 2,
        "current_approvals": 0,
        "approved_by": None,
        "rejected_by": None,
        "rejection_reason": None,
        "expires_at": None,
        "created_at": 1700000000000,
        "updated_at": 1700000000000,
    }


def _role_row():
    return {
        "id": BIG,
        "name": "support_admin",
        "description": "d",
        "permissions": '{"admin.roles":true}',
        "created_at": 1,
        "created_by": BIG + 1,
        "updated_at": None,
        "is_system": 0,
    }


def _admin_role_row():
    return (BIG, "support_admin", "d", "{}", 0, 1, BIG + 2)


def _client_factory(module, db, *, scope_error=False):
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/admin")

    def _gate(request, permission):
        if scope_error:
            raise HTTPException(status_code=403, detail="no scope")
        return 7

    patches = [
        patch.object(module, "check_host_restriction"),
        patch.object(module, "require_admin_permission", side_effect=_gate),
        patch("src.api.get_db", side_effect=lambda: db),
    ]
    for p in patches:
        p.start()
    client = TestClient(app)
    return client, patches


@pytest.fixture(autouse=True)
def _clean_cache():
    read_cache.clear_all()
    yield
    read_cache.clear_all()


# --------------------------------------------------------------------------- #
# identical output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module,row,path,key",
    [
        (approvals_route, _approval_row(), "/api/v1/admin/approvals", "approvals"),
        (roles_route, _role_row(), "/api/v1/admin/roles", "roles"),
        (
            roles_route,
            _admin_role_row(),
            f"/api/v1/admin/admins/{BIG + 9}/roles",
            "admin_roles",
        ),
    ],
)
def test_cached_output_is_identical(module, row, path, key):
    db = _DB([row])
    client, patches = _client_factory(module, db)
    try:
        fresh = client.get(path)
        assert fresh.status_code == 200, fresh.text
        read_cache.clear_all()
        cached = client.get(path)
    finally:
        for p in reversed(patches):
            p.stop()

    assert cached.status_code == 200
    assert cached.content == fresh.content, (cached.content, fresh.content)
    assert cached.json() == fresh.json()
    # Snowflake ids remain strings through the bytes cache.
    body = cached.json()
    if key == "approvals":
        assert body["approvals"][0]["id"] == str(BIG)
        assert body["approvals"][0]["requested_by"] == str(BIG + 1)
    elif key == "roles":
        assert body["roles"][0]["id"] == str(BIG)
        assert body["roles"][0]["created_by"] == str(BIG + 1)
    else:
        assert body["admin_id"] == str(BIG + 9)
        assert body["roles"][0]["assigned_by"] == str(BIG + 2)


# --------------------------------------------------------------------------- #
# performance — measured as work avoided, not flaky wall-clock
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module,row,path",
    [
        (approvals_route, _approval_row(), "/api/v1/admin/approvals"),
        (roles_route, _role_row(), "/api/v1/admin/roles"),
        (
            roles_route,
            _admin_role_row(),
            f"/api/v1/admin/admins/{BIG + 9}/roles",
        ),
    ],
)
def test_warm_call_skips_the_backing_query(module, row, path):
    db = _DB([row])
    client, patches = _client_factory(module, db)
    try:
        first = client.get(path)
        assert first.status_code == 200
        assert db.fetch_calls == 1, db.fetch_calls
        for _ in range(8):
            again = client.get(path)
            assert again.content == first.content
        # Only the cold miss touched the DB; eight warm polls were free.
        assert db.fetch_calls == 1, db.fetch_calls
    finally:
        for p in reversed(patches):
            p.stop()


# --------------------------------------------------------------------------- #
# security
# --------------------------------------------------------------------------- #


def test_scope_gate_runs_before_the_cache():
    db = _DB([_role_row()])
    # Warm the cache as an authorized caller.
    client, patches = _client_factory(roles_route, db)
    try:
        assert client.get("/api/v1/admin/roles").status_code == 200
    finally:
        for p in reversed(patches):
            p.stop()

    # A caller without the scope must still be rejected even though the body
    # is cached: the gate is evaluated before the lookup.
    denied, denied_patches = _client_factory(roles_route, db, scope_error=True)
    try:
        resp = denied.get("/api/v1/admin/roles")
    finally:
        for p in reversed(denied_patches):
            p.stop()
    assert resp.status_code == 403


def test_cache_is_scoped_to_the_backing_database():
    db_a = _DB([_role_row()])
    client_a, patches_a = _client_factory(roles_route, db_a)
    try:
        first = client_a.get("/api/v1/admin/roles")
        assert first.status_code == 200
        assert first.json()["roles"][0]["name"] == "support_admin"
    finally:
        for p in reversed(patches_a):
            p.stop()

    # A different DB object (repointed server / fresh test database) with a
    # different row must not observe the first DB's cached payload.
    other_row = dict(_role_row(), name="other_role")
    db_b = _DB([other_row])
    client_b, patches_b = _client_factory(roles_route, db_b)
    try:
        second = client_b.get("/api/v1/admin/roles")
    finally:
        for p in reversed(patches_b):
            p.stop()
    assert second.json()["roles"][0]["name"] == "other_role"


def test_role_mutation_invalidation_refreshes_the_cache():
    read_cache.clear_all()
    db = _DB([_role_row()])
    client, patches = _client_factory(roles_route, db)
    try:
        first = client.get("/api/v1/admin/roles")
        assert first.json()["roles"][0]["name"] == "support_admin"

        # Simulate the mutation committing new rows, then the mutation handler
        # invalidating the read cache exactly as create/assign/revoke do.
        db.rows = [dict(_role_row(), name="renamed_role")]
        read_cache.invalidate_admin_read_caches()

        second = client.get("/api/v1/admin/roles")
        assert second.json()["roles"][0]["name"] == "renamed_role"
        assert db.fetch_calls == 2
    finally:
        for p in reversed(patches):
            p.stop()


def test_approval_mutation_invalidation_refreshes_the_cache():
    read_cache.clear_all()
    db = _DB([_approval_row()])
    client, patches = _client_factory(approvals_route, db)
    try:
        first = client.get("/api/v1/admin/approvals")
        assert first.json()["approvals"][0]["status"] == "pending"

        db.rows = [dict(_approval_row(), status="approved")]
        read_cache.invalidate_approval_caches()

        second = client.get("/api/v1/admin/approvals")
        assert second.json()["approvals"][0]["status"] == "approved"
        assert db.fetch_calls == 2
    finally:
        for p in reversed(patches):
            p.stop()


def test_consuming_an_approval_invalidates_the_list_cache(monkeypatch):
    """A gated action that consumes an approval must refresh the cached list."""
    import src.api.routes.admin.utils as admin_utils

    class _GateDB:
        def fetch_one(self, query, params=None):
            return {"admin_count": 2, "approval_id": 4242}

        def execute(self, query, params=None):
            class _Cur:
                rowcount = 1

            return _Cur()

    calls = {"n": 0}
    real_invalidate = read_cache.invalidate_approval_caches

    def _spy():
        calls["n"] += 1
        real_invalidate()

    monkeypatch.setattr(read_cache, "invalidate_approval_caches", _spy)
    monkeypatch.setattr(
        admin_utils.config,
        "get",
        lambda key, default=None: (
            {"approval_workflows": {"single_admin_bypass": False}}
            if key == "admin_ui"
            else default
        ),
    )
    with patch("src.api.get_db", return_value=_GateDB()):
        assert admin_utils.require_approved_request("migrations.apply") == 4242
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "module,row,path",
    [
        (approvals_route, _approval_row(), "/api/v1/admin/approvals"),
        (roles_route, _role_row(), "/api/v1/admin/roles"),
        (
            roles_route,
            _admin_role_row(),
            f"/api/v1/admin/admins/{BIG + 9}/roles",
        ),
    ],
)
@pytest.mark.parametrize(
    "query",
    ["limit=100000", "limit=0", "offset=-1", "offset=100000000"],
)
def test_list_bounds_are_enforced(module, row, path, query):
    db = _DB([row])
    client, patches = _client_factory(module, db)
    try:
        resp = client.get(f"{path}?{query}")
    finally:
        for p in reversed(patches):
            p.stop()
    assert resp.status_code == 422, (query, resp.status_code, resp.text)


def test_bounded_query_uses_bound_parameters():
    """The LIMIT/OFFSET are bound parameters, never interpolated SQL."""
    seen = {}

    class _Capture(_DB):
        def fetch_all(self, query, params=None):
            seen["query"] = query
            seen["params"] = params
            return super().fetch_all(query, params)

    db = _Capture([_approval_row()])
    client, patches = _client_factory(approvals_route, db)
    try:
        assert (
            client.get("/api/v1/admin/approvals?limit=25&offset=5").status_code == 200
        )
    finally:
        for p in reversed(patches):
            p.stop()
    assert "LIMIT ? OFFSET ?" in seen["query"]
    assert seen["params"][-2:] == [25, 5] or seen["params"][-2:] == (25, 5)
    assert "25" not in seen["query"]
