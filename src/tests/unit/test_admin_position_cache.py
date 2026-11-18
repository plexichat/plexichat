"""Bounded role-position cache: hits, invalidation, and no stale grant.

``get_admin_max_position`` backs the admin hierarchy gates (``can_manage_admin``
/ ``can_manage_role``).  It is memoised in a bounded, db-scoped, short-TTL store
and must be dropped on every privilege mutation.  A stale *higher* position is
a stale grant, so a revoked/demoted role must take effect on the next decision.

Run with: pytest -m unit src/tests/unit/test_admin_position_cache.py
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.admin import permission_cache
from src.core.admin import permissions as perm_core
from src.core.admin import request_roles


pytestmark = pytest.mark.unit


class _DB:
    """DB double returning a configurable max position per admin id."""

    def __init__(self, positions):
        self.positions = dict(positions)
        self.calls = []

    def fetch_one(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        if "MAX(r.position)" in sql:
            aid = params[0] if params else None
            return {"max_pos": self.positions.get(aid, 0)}
        return None

    def fetch_all(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        return None


@pytest.fixture(autouse=True)
def _clear_cache():
    permission_cache.clear_position_cache()
    request_roles.clear_session_roles()
    yield
    permission_cache.clear_position_cache()
    request_roles.clear_session_roles()


def _patch_logger():
    logger = MagicMock()
    logger.log_action.return_value = True
    return (
        patch("utils.config.get", return_value={}),
        patch("src.core.admin.logging.get_admin_logger", return_value=logger),
        patch("src.core.admin.logging.AdminLogEntry", MagicMock()),
    )


def _log(db, admin_id, action):
    patches = _patch_logger()
    try:
        perm_core.log_admin_action(db, admin_id, action, "admin_role", 7)
    finally:
        for p in patches:
            p.stop()


def test_position_cache_hit_avoids_repeated_db_queries():
    db = _DB({42: 100})

    assert perm_core.get_admin_max_position(db, 42) == 100
    assert perm_core.get_admin_max_position(db, 42) == 100
    assert perm_core.get_admin_max_position(db, 42) == 100
    assert len(db.calls) == 1


def test_position_cache_is_scoped_to_the_database():
    db_a = _DB({42: 100})
    db_b = _DB({42: 10})

    assert perm_core.get_admin_max_position(db_a, 42) == 100
    assert perm_core.get_admin_max_position(db_b, 42) == 10
    assert len(db_a.calls) == 1
    assert len(db_b.calls) == 1


@pytest.mark.parametrize(
    "action",
    [
        "role.create",
        "role.update",
        "role.delete",
        "role.assign",
        "role.revoke",
        "admin_user.create",
        "admin_user.update",
        "admin_user.delete",
        "admin_user.toggle_status",
        "force_password_change",
        "approval.approve",
        "approval.reject",
        "approval.cancel",
    ],
)
def test_privileged_mutation_invalidates_position_cache(action):
    db = _DB({42: 100})
    assert perm_core.get_admin_max_position(db, 42) == 100
    assert len(db.calls) == 1

    # The mutation lowers the actor's position in the database.
    db.positions[42] = 5
    _log(db, 42, action)

    assert perm_core.get_admin_max_position(db, 42) == 5
    assert len(db.calls) == 2


def test_non_privileged_action_keeps_cached_position():
    db = _DB({42: 100})
    assert perm_core.get_admin_max_position(db, 42) == 100

    db.positions[42] = 5
    _log(db, 42, "admin.read")

    assert perm_core.get_admin_max_position(db, 42) == 100
    assert len(db.calls) == 1


def test_revocation_removes_stale_hierarchy_grant():
    db = _DB({1: 100, 2: 50})
    assert perm_core.get_admin_max_position(db, 1) == 100
    assert perm_core.can_manage_admin(db, 1, 2) is True

    # Revoking a high-position role from the actor demotes it. The formerly
    # cached 100 must not keep authorising management of admin 2.
    db.positions[1] = 10
    _log(db, 1, "role.revoke")

    assert perm_core.can_manage_admin(db, 1, 2) is False


def test_lookup_errors_fail_closed():
    class _Broken(_DB):
        def fetch_one(self, sql, params=()):
            raise RuntimeError("db down")

    assert perm_core.get_admin_max_position(_Broken({}), 1) == 0


def test_cache_is_bounded():
    for admin_id in range(permission_cache._MAX_ENTRIES + 25):
        perm_core.get_admin_max_position(_DB({}), admin_id)
    assert len(permission_cache._ENTRIES) <= permission_cache._MAX_ENTRIES
