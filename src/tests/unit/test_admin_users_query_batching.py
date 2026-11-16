"""Query-count regressions for admin-account mutations.

``update_admin_user``, ``delete_admin_user`` and ``toggle_admin_user_status``
used to issue a standalone existence query, ``can_manage_admin`` (two more
position queries) and, on the toggle path, a separate lock-state query.  The
batched helper resolves everything in one statement; these tests pin the
count and verify the hierarchy decision is unchanged.
"""

import asyncio
from types import SimpleNamespace

import pytest

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
    return SimpleNamespace(client=None)


def _patch(monkeypatch, admin_id=1):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: admin_id)
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **kw: True)


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
