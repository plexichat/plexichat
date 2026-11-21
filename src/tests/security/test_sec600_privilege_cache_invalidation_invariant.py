"""SEC600 invariant: every admin privilege mutation invalidates authz caches.

The admin permission caches are only safe because a successful privilege
mutation drops them on the very next authorization decision. Three choke points
form that chain:

* ``privilege_cache.invalidate_privilege_caches`` clears the user-auth cache
  prefixes *and* the bounded role-grant cache (a lazy import avoids the
  auth<->admin import cycle);
* ``permissions.invalidate_admin_permission_caches`` clears the request-scoped
  role payloads, the role-position cache and the grant cache;
* ``permissions.log_admin_action`` invokes that admin choke point for every
  action in ``PRIVILEGE_MUTATING_ACTIONS``, so a mutation cannot leave a
  just-revoked scope appearing granted (even if file/DB audit logging fails).

If any link is dropped, a stale cached verdict survives a revocation -- the
class of bug these fixes closed. This is a pure-AST ratchet: no app import,
config or DB is needed. Detectors are validated against synthetic good/bad
functions before the real tree is trusted, and a non-vacuity test proves the
scan sees the choke points and their mutation-route call sites.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
PRIVILEGE_CACHE = "src/core/auth/privilege_cache.py"
PERMISSIONS = "src/core/admin/permissions.py"
MUTATION_ROUTES = (
    "src/api/routes/admin/roles.py",
    "src/api/routes/admin/users/admin_users.py",
)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _loaded_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _call_names(node):
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _referenced(node):
    return _loaded_names(node) | _call_names(node)


def _collection_size(tree, name):
    """Size of a module-level tuple/list/set or ``frozenset({...})`` literal."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        value = node.value
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return len(value.elts)
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"frozenset", "set", "tuple", "list"}
            and value.args
            and isinstance(value.args[0], (ast.Tuple, ast.List, ast.Set))
        ):
            return len(value.args[0].elts)
    return None


def _auth_invalidation_missing(fn):
    names = _referenced(fn) if fn is not None else set()
    return [
        m
        for m in (
            "_AUTH_CACHE_PREFIXES",
            "invalidate_pattern",
            "invalidate_grant_cache",
        )
        if m not in names
    ]


def _admin_invalidation_missing(fn):
    names = _referenced(fn) if fn is not None else set()
    return [
        m
        for m in (
            "clear_session_roles",
            "invalidate_position_cache",
            "invalidate_grant_cache",
        )
        if m not in names
    ]


def _log_admin_action_missing(fn):
    names = _referenced(fn) if fn is not None else set()
    return [
        m
        for m in ("PRIVILEGE_MUTATING_ACTIONS", "invalidate_admin_permission_caches")
        if m not in names
    ]


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _invalidation_call_count(relative):
    tree = _tree(relative)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "invalidate_privilege_caches"
    )


_priv_tree = _tree(PRIVILEGE_CACHE)
_perms_tree = _tree(PERMISSIONS)
_SCAN = {
    "auth_missing": _auth_invalidation_missing(
        _find_function(_priv_tree, "invalidate_privilege_caches")
    ),
    "admin_missing": _admin_invalidation_missing(
        _find_function(_perms_tree, "invalidate_admin_permission_caches")
    ),
    "log_missing": _log_admin_action_missing(
        _find_function(_perms_tree, "log_admin_action")
    ),
    "prefix_count": _collection_size(_priv_tree, "_AUTH_CACHE_PREFIXES"),
    "mutating_count": _collection_size(_perms_tree, "PRIVILEGE_MUTATING_ACTIONS"),
    "route_calls": {rel: _invalidation_call_count(rel) for rel in MUTATION_ROUTES},
}


def test_self_test_detectors_catch_synthetic_violations():
    """Dropping any invalidation link is caught; the complete chain passes."""
    good_auth = textwrap.dedent(
        """
        def invalidate_privilege_caches():
            for pattern in _AUTH_CACHE_PREFIXES:
                invalidate_pattern(pattern)
            from src.core.admin.permission_grant_cache import invalidate_grant_cache
            invalidate_grant_cache()
        """
    )
    bad_auth = textwrap.dedent(
        """
        def invalidate_privilege_caches():
            for pattern in _AUTH_CACHE_PREFIXES:
                invalidate_pattern(pattern)
        """
    )
    assert (
        _auth_invalidation_missing(
            _find_function(ast.parse(good_auth), "invalidate_privilege_caches")
        )
        == []
    )
    assert _auth_invalidation_missing(
        _find_function(ast.parse(bad_auth), "invalidate_privilege_caches")
    ) == ["invalidate_grant_cache"]

    good_admin = textwrap.dedent(
        """
        def invalidate_admin_permission_caches():
            clear_session_roles()
            invalidate_position_cache()
            invalidate_grant_cache()
        """
    )
    bad_admin = textwrap.dedent(
        """
        def invalidate_admin_permission_caches():
            clear_session_roles()
        """
    )
    assert (
        _admin_invalidation_missing(
            _find_function(ast.parse(good_admin), "invalidate_admin_permission_caches")
        )
        == []
    )
    assert set(
        _admin_invalidation_missing(
            _find_function(ast.parse(bad_admin), "invalidate_admin_permission_caches")
        )
    ) == {"invalidate_position_cache", "invalidate_grant_cache"}

    good_log = textwrap.dedent(
        """
        def log_admin_action(action):
            if action in PRIVILEGE_MUTATING_ACTIONS:
                invalidate_admin_permission_caches()
        """
    )
    bad_log = textwrap.dedent(
        """
        def log_admin_action(action):
            if action in PRIVILEGE_MUTATING_ACTIONS:
                write_audit(action)
        """
    )
    assert (
        _log_admin_action_missing(
            _find_function(ast.parse(good_log), "log_admin_action")
        )
        == []
    )
    assert _log_admin_action_missing(
        _find_function(ast.parse(bad_log), "log_admin_action")
    ) == ["invalidate_admin_permission_caches"]


def test_scan_is_not_vacuous():
    assert _SCAN["prefix_count"] is not None and _SCAN["prefix_count"] >= 4
    assert _SCAN["mutating_count"] is not None and _SCAN["mutating_count"] >= 3
    assert sum(_SCAN["route_calls"].values()) >= 5, _SCAN["route_calls"]
    assert all(count > 0 for count in _SCAN["route_calls"].values()), _SCAN[
        "route_calls"
    ]


def test_user_auth_invalidation_also_drops_grant_cache():
    assert not _SCAN["auth_missing"], (
        "invalidate_privilege_caches lost a link:\n  "
        + "\n  ".join(_SCAN["auth_missing"])
    )


def test_admin_invalidation_clears_all_three_authorization_caches():
    assert not _SCAN["admin_missing"], (
        "invalidate_admin_permission_caches lost a clear:\n  "
        + "\n  ".join(_SCAN["admin_missing"])
    )


def test_privilege_mutations_invalidate_before_next_decision():
    assert not _SCAN["log_missing"], (
        "log_admin_action lost its mutation invalidation gate:\n  "
        + "\n  ".join(_SCAN["log_missing"])
    )
