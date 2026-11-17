"""Every admin role route must carry an explicit permission gate.

The role-management router is small enough to audit exhaustively: each route
funnels through ``_require_role_manager``, which in turn requires the
``admin.roles`` scope.  These tests enumerate the router so a handler added
without a gate (or with a drifted scope constant) fails loudly.
"""

import inspect

from src.api.routes.admin import roles as role_routes
from src.core.admin.permissions import PERMISSION_SCOPES


def test_role_manage_scope_is_a_declared_scope():
    assert role_routes.ROLE_MANAGE_SCOPE == "admin.roles"
    assert role_routes.ROLE_MANAGE_SCOPE in PERMISSION_SCOPES


def test_gate_helper_delegates_to_permission_check():
    source = inspect.getsource(role_routes._require_role_manager)
    assert "require_admin_permission(request, ROLE_MANAGE_SCOPE)" in source


def test_every_role_route_declares_the_permission_gate():
    routes = list(role_routes.router.routes)
    assert routes, "expected the roles router to expose routes"

    for route in routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:  # pragma: no cover - defensive
            continue
        source = inspect.getsource(endpoint)
        name = getattr(endpoint, "__name__", "?")
        assert "_require_role_manager(request)" in source, (
            f"{name} ({getattr(route, 'path', '?')}) has no role permission gate"
        )
        assert "check_host_restriction(request)" in source, (
            f"{name} ({getattr(route, 'path', '?')}) has no host restriction gate"
        )


def test_mutating_role_routes_are_all_covered():
    """Explicitly pin the mutating route set so a new write route is noticed."""
    mutating = {
        getattr(route, "endpoint").__name__
        for route in role_routes.router.routes
        if set(getattr(route, "methods", ())) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert {
        "create_role",
        "update_role",
        "delete_role",
        "assign_role",
        "revoke_role",
    } <= mutating
