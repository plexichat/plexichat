"""Regressions for two admin-account authorization gaps.

* ``list_admin_users`` fetched the whole admin roster with no LIMIT/OFFSET,
  unlike the bounded role/admin-role listings. One request could materialise an
  arbitrarily large result set (and, with enough admins, stall a worker).
* ``update_admin_user``/``delete_admin_user``/``toggle_admin_user_status``
  authorised against a single read snapshot and then wrote in separate,
  autocommitted statements with no re-check. A concurrent promotion of the
  target (or revocation from the actor) in that window let the stale,
  previously-authorised decision disable/delete/edit an admin that was no
  longer beneath the actor -- the same TOCTOU the role routes close with
  compare-and-swap guards.

All values in the guards are bound parameters; the SQL text is fixed.
"""

import asyncio
import inspect
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.admin import permissions as perm_core


pytestmark = pytest.mark.unit


class _Cursor:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _DB:
    """DB double that records queries and reports a controllable rowcount."""

    def __init__(
        self,
        hierarchy=None,
        one_rows=None,
        all_rows=None,
        execute_rowcount: int = 1,
    ):
        self.hierarchy = hierarchy or {
            "target_exists": True,
            "current_pos": 100,
            "target_pos": 50,
            "account_locked": 0,
        }
        self.one_rows = list(one_rows) if one_rows else []
        self.all_rows = [] if all_rows is None else all_rows
        self.execute_rowcount = execute_rowcount
        self.fetch_all_calls = []
        self.execute_calls = []

    def fetch_one(self, sql, params=()):
        if self.one_rows:
            return self.one_rows.pop(0)
        return self.hierarchy

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((" ".join(sql.split()), tuple(params)))
        return self.all_rows

    def execute(self, sql, params=()):
        self.execute_calls.append((" ".join(sql.split()), tuple(params)))
        return _Cursor(self.execute_rowcount)


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _patch(monkeypatch, db, admin_id=1):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: admin_id)
    # Approval enforcement is exercised in its own test module; here we bypass
    # it so these tests stay focused on the hierarchy gate.
    monkeypatch.setattr(au, "require_approved_request", lambda action, *a, **kw: None)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_auth", lambda: object())
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)
    monkeypatch.setattr(perm_core, "can_grant_existing_role", lambda *a, **kw: True)
    monkeypatch.setattr(au, "invalidate_privilege_caches", lambda: None)


class TestAdminListingIsBounded:
    def test_default_page_is_bounded(self, monkeypatch):
        db = _DB()
        _patch(monkeypatch, db)

        # Direct method calls carry the Query defaults as marker objects; the
        # router resolves them to ints. Pass the concrete defaults explicitly.
        asyncio.run(_Router().list_admin_users(_request(), limit=500, offset=0))

        assert len(db.fetch_all_calls) == 1
        sql, params = db.fetch_all_calls[0]
        assert "LIMIT ? OFFSET ?" in sql
        assert params == (500, 0)

    def test_explicit_page_is_used(self, monkeypatch):
        db = _DB()
        _patch(monkeypatch, db)

        asyncio.run(_Router().list_admin_users(_request(), limit=25, offset=40))

        assert db.fetch_all_calls[0][1] == (25, 40)

    def test_page_query_params_have_bounds(self):
        sig = inspect.signature(au.AdminUsersMixin.list_admin_users)
        for name, lo, hi in (("limit", 1, 1000), ("offset", 0, 1_000_000)):
            default = sig.parameters[name].default
            metadata = getattr(default, "metadata", None) or []
            lows = [getattr(c, "ge") for c in metadata if hasattr(c, "ge")]
            highs = [getattr(c, "le") for c in metadata if hasattr(c, "le")]
            if hasattr(default, "ge"):
                lows.append(default.ge)
            if hasattr(default, "le"):
                highs.append(default.le)
            assert lo in lows, (name, lows)
            assert hi in highs, (name, highs)


class TestAdminUserMutationWriteGuards:
    def test_toggle_reasserts_hierarchy_and_lock_state_at_write(self, monkeypatch):
        hierarchy = {
            "target_exists": True,
            "current_pos": 100,
            "target_pos": 50,
            "account_locked": 0,
        }
        db = _DB(hierarchy=hierarchy)
        _patch(monkeypatch, db)

        asyncio.run(_Router().toggle_admin_user_status(_request(), "2"))

        updates = [c for c in db.execute_calls if c[0].startswith("UPDATE auth_users")]
        assert len(updates) == 1
        sql, params = updates[0]
        # The hierarchy is recomputed from the database as the write executes.
        assert "SELECT COALESCE(MAX(r.position), 0)" in sql
        assert "account_locked = ?" in sql
        # new_status, target uid, observed lock state, then actor/target ids.
        assert params == (1, 2, 0, 1, 2)

    def test_toggle_losing_the_race_is_409_without_side_effects(self, monkeypatch):
        db = _DB(execute_rowcount=0)  # target promoted / concurrently toggled
        _patch(monkeypatch, db)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_Router().toggle_admin_user_status(_request(), "2"))

        assert exc.value.status_code == 409
        assert not any(
            "DELETE FROM admin_sessions" in sql for sql, _ in db.execute_calls
        )

    def test_delete_reasserts_hierarchy_before_cascading_deletes(self, monkeypatch):
        db = _DB()
        _patch(monkeypatch, db)

        asyncio.run(_Router().delete_admin_user(_request(), "2"))

        guards = [c for c in db.execute_calls if "SET id = id" in c[0]]
        assert len(guards) == 1
        sql, params = guards[0]
        assert "SELECT COALESCE(MAX(r.position), 0)" in sql
        assert params == (2, 1, 2)
        # The guard runs before any destructive statement.
        assert guards[0] == db.execute_calls[0]

    def test_delete_losing_the_race_is_409_without_deletes(self, monkeypatch):
        db = _DB(execute_rowcount=0)  # target promoted concurrently
        _patch(monkeypatch, db)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_Router().delete_admin_user(_request(), "2"))

        assert exc.value.status_code == 409
        assert not any(sql.startswith("DELETE") for sql, _ in db.execute_calls)

    def test_role_change_reasserts_hierarchy(self, monkeypatch):
        db = _DB(
            one_rows=[
                {
                    "target_exists": True,
                    "current_pos": 100,
                    "target_pos": 50,
                    "account_locked": 0,
                },
                {
                    "role_id": 5,
                    "role_pos": 10,
                    "role_system": 0,
                    "current_pos": 100,
                },
            ]
        )
        _patch(monkeypatch, db)

        asyncio.run(
            _Router().update_admin_user(
                _request(), "2", au.AdminUserUpdate(role="moderator")
            )
        )

        guards = [c for c in db.execute_calls if "SET id = id" in c[0]]
        assert len(guards) == 1
        assert "SELECT COALESCE(MAX(r.position), 0)" in guards[0][0]
        asserts = [
            c
            for c in db.execute_calls
            if c[0].startswith("INSERT INTO admin_role_assignments")
        ]
        assert len(asserts) == 1
        # Guard precedes the privilege-changing write.
        assert db.execute_calls.index(guards[0]) < db.execute_calls.index(asserts[0])
