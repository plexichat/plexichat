"""Query-count regressions for admin-account mutations.

``update_admin_user``, ``delete_admin_user`` and ``toggle_admin_user_status``
used to issue a standalone existence query, ``can_manage_admin`` (two more
position queries) and, on the toggle path, a separate lock-state query.  The
batched helper resolves everything in one statement; these tests pin the
count and verify the hierarchy decision is unchanged.
"""

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

import src.api as api
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.admin import permissions as perm_core


class _CountingDB:
    def __init__(self, row, execute_error=None):
        self.row = row
        self.fetch_calls = []
        self.execute_calls = []
        self.execute_error = execute_error

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return self.row

    def fetch_all(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return []

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params)))


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request():
    return cast(Request, SimpleNamespace(client=None))


def _patch(monkeypatch, admin_id=1):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: admin_id)
    # Approval enforcement is exercised in its own test module; here we bypass
    # it so these tests stay focused on query counts.
    monkeypatch.setattr(au, "require_approved_request", lambda action, *a, **kw: None)
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)
    monkeypatch.setattr(perm_core, "can_grant_existing_role", lambda *a, **kw: True)


@pytest.mark.unit
def test_load_admin_hierarchy_is_one_query_for_both_row_shapes():
    for tuple_row in (False, True):
        row = (
            (1, 100, 50, 0)
            if tuple_row
            else {
                "target_exists": 1,
                "current_pos": 100,
                "target_pos": 50,
                "account_locked": 0,
            }
        )
        db = _CountingDB(row)
        result = au._load_admin_hierarchy(db, 1, 2)

        assert len(db.fetch_calls) == 1
        assert db.fetch_calls[0][1] == (2, 1, 2, 2)
        assert result == {
            "target_exists": True,
            "current_pos": 100,
            "target_pos": 50,
            "account_locked": 0,
        }


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,target_pos,current_id,target_id",
    [(100, 50, 1, 2), (50, 100, 1, 2), (50, 50, 1, 2), (100, 100, 7, 7)],
)
def test_admin_decision_matches_core_helper(
    monkeypatch, current_pos, target_pos, current_id, target_id
):
    monkeypatch.setattr(
        perm_core,
        "get_admin_max_position",
        lambda db, aid: current_pos if aid == current_id else target_pos,
    )
    expected = perm_core.can_manage_admin(object(), current_id, target_id)
    assert (
        au._can_manage_admin_at(current_id, target_id, current_pos, target_pos)
        is expected
    )


@pytest.mark.unit
def test_update_admin_user_uses_one_hierarchy_lookup(monkeypatch):
    db = _CountingDB(
        {"target_exists": 1, "current_pos": 100, "target_pos": 50, "account_locked": 0}
    )
    _patch(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_auth", lambda: object())
    router = _Router()

    asyncio.run(router.update_admin_user(_request(), "2", au.AdminUserUpdate()))

    hierarchy_calls = [c for c in db.fetch_calls if "AS target_exists" in c[0]]
    assert len(hierarchy_calls) == 1
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_toggle_status_uses_one_hierarchy_lookup_including_lock_state(monkeypatch):
    db = _CountingDB(
        {"target_exists": 1, "current_pos": 100, "target_pos": 50, "account_locked": 1}
    )
    _patch(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)
    router = _Router()

    asyncio.run(router.toggle_admin_user_status(_request(), "2"))

    hierarchy_calls = [c for c in db.fetch_calls if "AS target_exists" in c[0]]
    assert len(hierarchy_calls) == 1
    assert len(db.fetch_calls) == 1
    update = [c for c in db.execute_calls if "UPDATE auth_users" in c[0]]
    assert len(update) == 1
    # Locked -> unlocked.
    assert update[0][1][0] == 0


@pytest.mark.unit
def test_delete_admin_user_uses_one_hierarchy_lookup(monkeypatch):
    db = _CountingDB(
        {"target_exists": 1, "current_pos": 100, "target_pos": 50, "account_locked": 0}
    )
    _patch(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)
    router = _Router()

    asyncio.run(router.delete_admin_user(_request(), "2"))

    hierarchy_calls = [c for c in db.fetch_calls if "AS target_exists" in c[0]]
    assert len(hierarchy_calls) == 1
    assert len(db.fetch_calls) == 1


class _SeqDB:
    """DB double returning queued rows in call order."""

    def __init__(self, rows):
        self.queue = list(rows)
        self.fetch_calls = []
        self.execute_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return self.queue.pop(0) if self.queue else None

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params)))


def _role_ctx(role_id=5, role_pos=10, system=0, current_pos=100):
    return {
        "role_id": role_id,
        "role_pos": role_pos,
        "role_system": system,
        "current_pos": current_pos,
    }


@pytest.mark.unit
def test_load_role_for_delegation_is_one_query():
    db = _CountingDB(_role_ctx())
    result = au._load_role_for_delegation(db, "moderator", 1)
    assert len(db.fetch_calls) == 1
    assert db.fetch_calls[0][1] == ("moderator", "moderator", "moderator", 1)
    assert result["role_id"] == 5
    assert result["current_pos"] == 100


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,role_pos,system",
    [
        (100, 50, False),
        (50, 50, False),
        (100, 0, True),
        (99, 0, True),
        (100, None, False),
    ],
)
def test_role_decision_matches_core_helper(monkeypatch, current_pos, role_pos, system):
    row = None if role_pos is None else {"position": role_pos, "is_system": int(system)}

    class _Db:
        def fetch_one(self, sql, params=()):
            return row

    monkeypatch.setattr(
        perm_core, "get_admin_max_position", lambda db, aid: current_pos
    )
    expected = perm_core.can_manage_role(_Db(), 1, 3)
    assert au._can_manage_role_at(current_pos, role_pos, system) is expected


@pytest.mark.unit
def test_create_admin_user_batches_role_lookup_with_hierarchy(monkeypatch):
    db = _SeqDB([_role_ctx(), None, {"password_hash": "hash"}])
    _patch(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)

    def _unexpected(*args, **kwargs):
        raise AssertionError("create_admin_user must use the batched role lookup")

    monkeypatch.setattr(perm_core, "can_manage_role", _unexpected)

    class _Auth:
        def register(self, **kwargs):
            return SimpleNamespace(id=99)

    monkeypatch.setattr(api, "get_auth", lambda: _Auth())

    asyncio.run(
        _Router().create_admin_user(
            _request(),
            au.AdminUserCreate(
                username="mod", email="mod@example.com", password="pw", role="moderator"
            ),
        )
    )

    role_calls = [c for c in db.fetch_calls if "AS role_id" in c[0]]
    assert len(role_calls) == 1
    assert len(db.fetch_calls) == 3


@pytest.mark.unit
def test_update_role_change_batches_role_lookup_with_hierarchy(monkeypatch):
    db = _SeqDB(
        [
            {
                "target_exists": 1,
                "current_pos": 100,
                "target_pos": 50,
                "account_locked": 0,
            },
            _role_ctx(),
        ]
    )
    _patch(monkeypatch)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_auth", lambda: object())

    asyncio.run(
        _Router().update_admin_user(
            _request(), "2", au.AdminUserUpdate(role="moderator")
        )
    )

    role_calls = [c for c in db.fetch_calls if "AS role_id" in c[0]]
    assert len(role_calls) == 1
    assert len(db.fetch_calls) == 2


@pytest.mark.unit
def test_admin_user_query_uses_set_based_role_ranking():
    sql = au._admin_user_query("ORDER BY a.created_at DESC")

    # The old projection correlated a scalar subquery on every admin row.
    assert "ra.admin_id = a.id" not in sql
    assert "ROW_NUMBER() OVER" in sql

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE admin_users (
            id INTEGER PRIMARY KEY, username TEXT, email TEXT,
            created_at INTEGER, last_login INTEGER
        );
        CREATE TABLE auth_users (
            id INTEGER PRIMARY KEY, account_locked INTEGER DEFAULT 0, last_login INTEGER
        );
        CREATE TABLE admin_roles (
            id INTEGER PRIMARY KEY, name TEXT, position INTEGER
        );
        CREATE TABLE admin_role_assignments (admin_id INTEGER, role_id INTEGER);
        INSERT INTO admin_users VALUES (1, 'a', 'a@x', 1, 11);
        INSERT INTO admin_users VALUES (2, 'b', 'b@x', 2, 22);
        INSERT INTO admin_users VALUES (3, 'c', 'c@x', 3, 33);
        INSERT INTO auth_users VALUES (1, 0, 11);
        INSERT INTO auth_users VALUES (2, 1, 22);
        INSERT INTO auth_users VALUES (3, 0, 33);
        INSERT INTO admin_roles VALUES (10, 'support_admin', 5);
        INSERT INTO admin_roles VALUES (11, 'moderator', 20);
        INSERT INTO admin_role_assignments VALUES (1, 10);
        INSERT INTO admin_role_assignments VALUES (1, 11);
        INSERT INTO admin_role_assignments VALUES (2, 10);
        """
    )
    rows = conn.execute(sql).fetchall()
    by_id = {row["id"]: row for row in rows}

    assert by_id[1]["role"] == "moderator"  # highest position wins
    assert by_id[2]["role"] == "support_admin"
    assert by_id[3]["role"] == "unassigned"
    assert by_id[2]["account_locked"] == 1
    conn.close()
