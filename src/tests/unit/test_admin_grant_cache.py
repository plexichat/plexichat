"""Bounded (db, credential, subject) role-grant cache regression suite.

``get_admin_granted_permissions`` feeds the delegation checks
(``can_delegate_permissions`` / ``can_grant_existing_role``) that decide whether
an admin may hand out a scope.  It resolves the admin's role payloads through
``_get_admin_roles`` and is called repeatedly within one privileged request, so
it is memoised in a bounded, short-TTL, credential-partitioned store that every
privilege mutation drops.  A stale scope is a stale grant, so a revocation must
take effect on the very next decision.

Run with: pytest -m unit src/tests/unit/test_admin_grant_cache.py
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.admin import permission_grant_cache as grant_cache
from src.core.admin import permissions as perm_core
from src.core.admin import request_roles


pytestmark = pytest.mark.unit


class _RoleDB:
    """DB double: configurable granted scopes per admin, counts role queries."""

    def __init__(self, grants=None):
        self.grants = {k: dict(v) for k, v in (grants or {}).items()}
        self.role_queries = 0

    def fetch_all(self, sql, params=()):
        if "admin_role_assignments" not in sql:
            return []
        self.role_queries += 1
        admin_id = params[0] if params else None
        permissions = self.grants.get(admin_id, {})
        return [
            {
                "id": 1,
                "name": "role",
                "description": "",
                "permissions": permissions,
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
def _clear_cache():
    grant_cache.clear_grant_cache()
    request_roles.clear_session_roles()
    yield
    grant_cache.clear_grant_cache()
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


def test_cache_hit_reduces_repeated_role_queries():
    """Three grant checks in one request issue exactly one role query."""
    db = _RoleDB({42: {"users.read": True, "admin.roles": True}})

    first = perm_core.get_admin_granted_permissions(db, 42)
    second = perm_core.get_admin_granted_permissions(db, 42)
    third = perm_core.get_admin_granted_permissions(db, 42)

    assert first == second == third == frozenset({"users.read", "admin.roles"})
    assert db.role_queries == 1, (
        "query-count regression: repeated grant lookups must hit the cache "
        f"(expected 1 role query, got {db.role_queries})"
    )


def test_query_reduction_is_measured_across_a_delegation_check():
    """The real can_delegate_permissions path no longer re-reads the roles."""
    db = _RoleDB({1: {"admin.roles": True, "users.read": True}})
    # Warm the cache the way the permission gate does at request start.
    perm_core.get_admin_granted_permissions(db, 1)
    baseline = db.role_queries

    assert perm_core.can_delegate_permissions(db, 1, {"users.read": True}) is True
    assert db.role_queries == baseline, (
        "delegation must reuse cached grants, not re-query roles "
        f"(extra queries: {db.role_queries - baseline})"
    )


def test_cache_is_scoped_to_db_credential_and_subject():
    db_a = _RoleDB({42: {"users.read": True}})
    db_b = _RoleDB({42: {"users.delete": True}})

    # Same subject on a different database never reuses the grant set.
    assert perm_core.get_admin_granted_permissions(db_a, 42) == frozenset(
        {"users.read"}
    )
    assert perm_core.get_admin_granted_permissions(db_b, 42) == frozenset(
        {"users.delete"}
    )
    assert db_a.role_queries == 1 and db_b.role_queries == 1

    # A different credential for the same subject/db also gets its own entry.
    request_roles.remember_session_credential("credential-alpha")
    assert perm_core.get_admin_granted_permissions(db_a, 42) == frozenset(
        {"users.read"}
    )
    assert db_a.role_queries == 2
    request_roles.remember_session_credential("credential-beta")
    assert perm_core.get_admin_granted_permissions(db_a, 42) == frozenset(
        {"users.read"}
    )
    assert db_a.role_queries == 3

    # Different subjects never collide.
    assert perm_core.get_admin_granted_permissions(db_a, 43) == frozenset()
    assert db_a.role_queries == 4


@pytest.mark.parametrize(
    "action",
    sorted(perm_core.PRIVILEGE_MUTATING_ACTIONS),
)
def test_every_privilege_mutation_invalidates_grant_cache(action):
    """role/assign/revoke/ban (and every sibling) drop cached grants."""
    db = _RoleDB({42: {"admin.roles": True}})
    assert perm_core.get_admin_granted_permissions(db, 42) == frozenset({"admin.roles"})
    assert db.role_queries == 1

    # The mutation strips the scope in the database.
    db.grants[42] = {}
    _log(db, 42, action)

    assert perm_core.get_admin_granted_permissions(db, 42) == frozenset()
    assert db.role_queries == 2, (
        f"{action} must invalidate the grant cache so the cached grant is gone"
    )


def test_auth_invalidation_helper_also_drops_grant_cache():
    """Routes calling invalidate_privilege_caches() on a mutation are covered."""
    from src.core.auth.privilege_cache import invalidate_privilege_caches

    db = _RoleDB({42: {"admin.roles": True}})
    assert perm_core.get_admin_granted_permissions(db, 42) == frozenset({"admin.roles"})
    db.grants[42] = {}
    with patch("src.core.database.invalidate_pattern"):
        invalidate_privilege_caches()
    assert perm_core.get_admin_granted_permissions(db, 42) == frozenset()
    assert db.role_queries == 2


def test_revocation_removes_stale_grant():
    """A cached grant must not survive revoking the role that carried it."""
    db = _RoleDB({1: {"admin.roles": True, "admin.edit": True}, 2: {}})
    assert perm_core.can_delegate_permissions(db, 1, {"admin.edit": True}) is True
    assert db.role_queries == 1

    # Admin 1 loses admin.edit (role revoked / edited). The previously cached
    # grant must not keep authorising delegation of that scope.
    db.grants[1] = {"admin.roles": True}
    _log(db, 1, "role.revoke")

    assert perm_core.can_delegate_permissions(db, 1, {"admin.edit": True}) is False
    assert db.role_queries == 2


def test_lookup_errors_fail_closed():
    class _Broken(_RoleDB):
        def fetch_all(self, sql, params=()):
            raise RuntimeError("db down")

    assert perm_core.get_admin_granted_permissions(_Broken(), 1) == frozenset()
    assert (
        perm_core.can_delegate_permissions(_Broken(), 1, {"admin.edit": True}) is False
    )


def test_non_boolean_payload_values_are_not_grants():
    db = _RoleDB({1: {"admin.edit": "true", "admin.roles": 1, "users.read": True}})
    assert perm_core.get_admin_granted_permissions(db, 1) == frozenset({"users.read"})


def test_cache_is_bounded():
    for admin_id in range(grant_cache._MAX_ENTRIES + 25):
        perm_core.get_admin_granted_permissions(_RoleDB(), admin_id)
    assert len(grant_cache._ENTRIES) <= grant_cache._MAX_ENTRIES
