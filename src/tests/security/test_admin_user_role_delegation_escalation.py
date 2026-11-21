"""Object-level delegation on the admin-account role-assignment paths.

``roles.py:assign_role`` refuses to hand out a role whose payload carries a
scope the actor does not hold (privilege laundering).  The parallel role
assignment paths in ``admin_users.py`` -- creating an admin account with a role
and retargeting an existing one's role -- had only the position-hierarchy gate.
An ``admin.edit`` holder could therefore create a new admin with a low-position
role carrying scopes it did not hold, then sign in as that account and use them.
"""

import asyncio
import json
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


class _SeqDB:
    """DB double returning queued rows in call order."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.fetch_calls = []
        self.execute_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((" ".join(sql.split()), tuple(params)))
        return self.rows.pop(0) if self.rows else None

    def execute(self, sql, params=()):
        self.execute_calls.append((" ".join(sql.split()), tuple(params)))
        return _Cursor()


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _role_ctx():
    # Role sits well below the actor, so the hierarchy gate alone would allow it.
    return {"role_id": 7, "role_pos": 5, "role_system": 0, "current_pos": 100}


def _hierarchy():
    return {
        "target_exists": 1,
        "current_pos": 100,
        "target_pos": 50,
        "account_locked": 0,
    }


def _patch(monkeypatch, db, held):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: 1)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(
        api,
        "get_auth",
        lambda: SimpleNamespace(register=lambda **kwargs: SimpleNamespace(id=99)),
    )
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)
    monkeypatch.setattr(au, "invalidate_privilege_caches", lambda: None)
    monkeypatch.setattr(
        perm_core,
        "get_admin_granted_permissions",
        lambda db, admin_id: frozenset(held),
    )


def _assigned(db) -> bool:
    return any(
        "INSERT INTO admin_role_assignments" in sql for sql, _ in db.execute_calls
    )


class TestCreateAdminUserDelegation:
    def test_role_carrying_unheld_scope_is_denied(self, monkeypatch):
        db = _SeqDB(
            [
                _role_ctx(),  # _load_role_for_delegation
                {"permissions": json.dumps({"config.security": True})},
                None,  # username collision check
                {"password_hash": "hash"},  # newly registered user
            ]
        )
        _patch(monkeypatch, db, held={"admin.read", "admin.edit"})

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                _Router().create_admin_user(
                    _request(),
                    au.AdminUserCreate(
                        username="mod",
                        email="mod@example.com",
                        password="pw",
                        role="moderator",
                    ),
                )
            )
        assert exc.value.status_code == 403
        assert not _assigned(db)

    def test_role_carrying_only_held_scopes_is_allowed(self, monkeypatch):
        db = _SeqDB(
            [
                _role_ctx(),
                {"permissions": json.dumps({"admin.read": True, "admin.edit": True})},
                None,
                {"password_hash": "hash"},
            ]
        )
        _patch(monkeypatch, db, held={"admin.read", "admin.edit"})

        result = asyncio.run(
            _Router().create_admin_user(
                _request(),
                au.AdminUserCreate(
                    username="mod",
                    email="mod@example.com",
                    password="pw",
                    role="moderator",
                ),
            )
        )
        assert result.success is True
        assert _assigned(db)


class TestUpdateAdminUserDelegation:
    def test_role_change_to_unheld_scope_is_denied(self, monkeypatch):
        db = _SeqDB(
            [
                _hierarchy(),
                _role_ctx(),
                {"permissions": json.dumps({"config.security": True})},
            ]
        )
        _patch(monkeypatch, db, held={"admin.read", "admin.edit"})

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                _Router().update_admin_user(
                    _request(), "2", au.AdminUserUpdate(role="moderator")
                )
            )
        assert exc.value.status_code == 403
        assert not _assigned(db)

    def test_role_change_to_held_scopes_is_allowed(self, monkeypatch):
        db = _SeqDB(
            [
                _hierarchy(),
                _role_ctx(),
                {"permissions": json.dumps({"admin.read": True, "admin.edit": True})},
            ]
        )
        _patch(monkeypatch, db, held={"admin.read", "admin.edit"})

        result = asyncio.run(
            _Router().update_admin_user(
                _request(), "2", au.AdminUserUpdate(role="moderator")
            )
        )
        assert result.success is True
        assert _assigned(db)
