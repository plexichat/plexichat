"""Privileged mutations must invalidate the request-scoped role cache.

``check_permission`` reuses the role payloads captured at session validation
(``cached_session_roles``) instead of querying ``admin_roles`` again.  The
routes' ``invalidate_privilege_caches()`` call only clears the memoised *user*
auth context -- never this request-local role snapshot.  Without the fix,
a role/permission mutation followed by any further authorization decision in
the same context is decided against the pre-mutation privileges: a just-revoked
scope still appears granted.

``log_admin_action`` is called by every privileged mutation route after the
write, so it is the choke point that drops the stale snapshot.

Run with: pytest -m unit src/tests/unit/test_admin_permission_cache_coherency.py
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.admin import permissions as perm_core
from src.core.admin import request_roles


pytestmark = pytest.mark.unit


class _DB:
    """DB double: fresh role reads return the post-mutation permission set."""

    def __init__(self, role_permissions):
        self.role_permissions = role_permissions
        self.all_calls = []

    def fetch_all(self, sql, params=()):
        self.all_calls.append((" ".join(sql.split()), tuple(params)))
        return [
            {
                "id": 1,
                "name": "role",
                "description": "",
                "permissions": self.role_permissions,
                "created_at": 0,
                "created_by": 0,
                "is_system": 0,
            }
        ]

    def fetch_one(self, sql, params=()):
        return None

    def execute(self, sql, params=()):
        return None


@pytest.fixture(autouse=True)
def _clear_roles():
    request_roles.clear_session_roles()
    yield
    request_roles.clear_session_roles()


def _patch_logger():
    logger = MagicMock()
    logger.log_action.return_value = True
    return (
        patch("utils.config.get", return_value={}),
        patch("src.core.admin.logging.get_admin_logger", return_value=logger),
        patch("src.core.admin.logging.AdminLogEntry", MagicMock()),
    )


@pytest.mark.parametrize(
    "action",
    [
        "role.update",
        "role.assign",
        "role.revoke",
        "role.create",
        "role.delete",
        "admin_user.update",
        "admin_user.delete",
        "admin_user.toggle_status",
        "force_password_change",
        "approval.approve",
    ],
)
def test_privileged_mutation_drops_stale_session_roles(action):
    # The session was validated while the admin still held admin.read; the
    # mutation has since revoked that scope in the database.
    db = _DB(role_permissions={"admin.read": False})
    request_roles.remember_session_roles(db, 42, ['{"admin.read": true}'])

    # Precondition: the gate trusts the cached grant and issues no query.
    assert perm_core.check_permission(42, "admin.read", db).has_permission is True
    assert db.all_calls == []
    assert request_roles.cached_session_roles(db, 42) is not None

    patches = _patch_logger()
    try:
        perm_core.log_admin_action(db, 42, action, "admin_role", 7)
    finally:
        for p in patches:
            p.stop()

    # The stale snapshot is gone and the gate now reflects the fresh role data.
    assert request_roles.cached_session_roles(db, 42) is None
    assert perm_core.check_permission(42, "admin.read", db).has_permission is False
    assert any("FROM admin_roles r" in sql for sql, _ in db.all_calls)


def test_non_privileged_action_keeps_the_cached_roles():
    db = _DB(role_permissions={"admin.read": False})
    request_roles.remember_session_roles(db, 42, ['{"admin.read": true}'])

    patches = _patch_logger()
    try:
        perm_core.log_admin_action(db, 42, "admin.read", "system", None)
    finally:
        for p in patches:
            p.stop()

    # A read-style audit entry must not evict the request's role snapshot.
    assert request_roles.cached_session_roles(db, 42) == ('{"admin.read": true}',)
    assert perm_core.check_permission(42, "admin.read", db).has_permission is True


def test_explicit_invalidation_helper_is_idempotent():
    db = _DB(role_permissions={"admin.read": True})
    request_roles.remember_session_roles(db, 42, ['{"admin.read": true}'])

    perm_core.invalidate_admin_permission_caches()
    perm_core.invalidate_admin_permission_caches()

    assert request_roles.cached_session_roles(db, 42) is None
