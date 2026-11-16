"""Every admin route must declare an authorization gate.

This is a structural audit of the aggregated admin router.  It walks included
sub-routers (FastAPI wraps them in ``_IncludedRouter``), reads each endpoint's
source and asserts that:

* every route authenticates (or is an explicitly justified public route); and
* every *mutating* route declares a concrete permission scope (or is an
  explicitly justified self-service/auth-flow route).

A newly added admin route with no gate therefore fails the suite instead of
silently expanding the unauthenticated attack surface.
"""

import inspect

from src.api.routes.admin import router as admin_router

# Patterns that prove a handler performs an authorization step.  Older routes
# authenticate with ``get_admin_from_token``; newer ones use the combined
# ``require_admin_permission`` helper or a module-local wrapper.
AUTH_GATES = (
    "require_admin_permission(",
    "get_admin_from_token(",
    "check_admin_permission(",
    "_require_role_manager(",
    "is_authenticated_self_test(",
    "require_approved_request(",
    "_require_admin(",
    "get_admin_user",
)

# Only these endpoints may be reached without any authorization gate.  They
# are the login/bootstrap pages, static admin-UI assets and the login OTP
# step; every one is public by design (host restriction still applies to the
# UI routes).
PUBLIC_EXEMPTIONS = {
    "admin_chart_js": "public admin UI static asset",
    "admin_dashboard_css": "public admin UI static asset",
    "admin_dashboard_js": "public admin UI static asset",
    "admin_dashboard_page": "public admin UI shell (gated client-side)",
    "admin_favicon": "public admin UI static asset",
    "admin_login": "authentication bootstrap",
    "admin_login_page": "public login page",
    "admin_logout": "authentication teardown",
    "admin_migrations_page": "public admin UI shell (gated client-side)",
    "admin_root": "redirect to the public login page",
    "admin_ui_redirect": "redirect to the public admin UI shell",
    "verify_otp": "second step of the authentication bootstrap",
}

# POST routes that are self-service authentication flows (a freshly logging
# in admin has no session scope yet) or read-only validators despite the verb.
MUTATING_SCOPE_EXEMPTIONS = {
    "admin_begin_otp_setup": "self-service OTP setup",
    "admin_change_password": "self-service password change",
    "admin_disable_otp": "self-service OTP disable",
    "admin_login": "authentication bootstrap",
    "admin_logout": "authentication teardown",
    "admin_regenerate_backup_codes": "self-service backup codes",
    "validate_license_key": "read-only license payload validation",
    "verify_otp": "authentication bootstrap",
}

SCOPE_GATES = ("require_admin_permission(", "_require_role_manager(")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _iter_admin_routes():
    """Yield (method set, path, endpoint) for every route in the admin router."""

    def walk(router):
        for route in router.routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from walk(included)
                continue
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            yield (
                set(getattr(route, "methods", ())),
                getattr(route, "path", ""),
                endpoint,
            )

    yield from walk(admin_router)


def _source(endpoint) -> str:
    try:
        return inspect.getsource(endpoint)
    except (OSError, TypeError):  # pragma: no cover - defensive
        return ""


def test_admin_router_enumeration_is_not_vacuous():
    routes = list(_iter_admin_routes())
    assert len(routes) >= 100, "admin route enumeration unexpectedly empty"


def test_every_admin_route_declares_an_authorization_gate():
    missing = []
    for methods, path, endpoint in _iter_admin_routes():
        source = _source(endpoint)
        name = getattr(endpoint, "__name__", "?")
        if name in PUBLIC_EXEMPTIONS:
            continue
        if not any(gate in source for gate in AUTH_GATES):
            missing.append(f"{', '.join(sorted(methods))} {path} ({name})")
    assert not missing, "admin routes without an authorization gate: " + ", ".join(
        sorted(missing)
    )


def test_every_mutating_admin_route_declares_a_permission_scope():
    missing = []
    for methods, path, endpoint in _iter_admin_routes():
        if not methods & MUTATING_METHODS:
            continue
        name = getattr(endpoint, "__name__", "?")
        if name in MUTATING_SCOPE_EXEMPTIONS:
            continue
        source = _source(endpoint)
        if not any(gate in source for gate in SCOPE_GATES):
            missing.append(f"{', '.join(sorted(methods))} {path} ({name})")
    assert not missing, "mutating admin routes without a declared scope: " + ", ".join(
        sorted(missing)
    )


def test_exemption_lists_are_explicit_and_narrow():
    # New routes must not be able to opt out by editing a broad wildcard.
    assert "*" not in PUBLIC_EXEMPTIONS
    assert "*" not in MUTATING_SCOPE_EXEMPTIONS
    assert len(PUBLIC_EXEMPTIONS) <= 20
    assert len(MUTATING_SCOPE_EXEMPTIONS) <= 12
    # Every exempted mutation is also a public/auth-flow route.
    assert set(MUTATING_SCOPE_EXEMPTIONS) <= set(PUBLIC_EXEMPTIONS) | {
        "admin_change_password",
        "admin_begin_otp_setup",
        "admin_disable_otp",
        "admin_regenerate_backup_codes",
        "validate_license_key",
    }
