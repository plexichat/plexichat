"""Cross-cutting guard: every state-changing API route must be authenticated.

FastAPI does not enforce auth globally in this project. Each mutating route
either declares an auth-capable dependency (``Depends(get_current_user)``,
``Depends(get_admin_access)``, ...), calls an auth/permission guard from its
handler (the class-based admin routes call ``require_admin_permission`` via a
``_require_*`` helper), or is one of a small, deliberately public flows.

This module enumerates the *whole* mounted route tree (including lazily
included sub-routers) and fails on any mutating route that has none of those.
The public set is a ratchet: a new unauthenticated mutation fails the suite,
and an entry that stops being a route (or gains a guard) is flagged as stale
so the list cannot decay into fiction.
"""

import ast
import inspect
import re
import textwrap

from src.api.routes import create_api_router

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Dependency callable names that establish an authenticated principal or a
# permission scope. Matches sub-dependencies too (FastAPI builds a tree).
_AUTH_DEP_TOKENS = (
    "current_user",
    "admin_user",
    "admin_access",
    "require",
    "auth",
    "permission",
    "token",
    "security",
    "scope",
    "session",
    "principal",
    "identity",
)

# Calls that prove the handler checks the caller itself. The admin API uses
# ``_require_admin_permission`` wrappers rather than FastAPI dependencies;
# webhook/federation handlers verify signatures/tokens in-path.
#
# Matched against the WHOLE called name via ``fullmatch`` below. An unanchored
# substring search over the raw endpoint source let response-model constructors
# such as ``PasskeyAuthenticateOptionsResponse(...)`` and cache-key builders
# such as ``authenticated_cache_key(...)`` masquerade as auth gates (the latter
# also matched merely via a route path containing "authenticate"), making this
# invariant vacuous. Keep the vocabulary anchored, mirroring the SEC100/SEC200/
# SEC300 sweeps. ``check_host_restriction`` is an admin in-handler guard that
# this cross-cutting sweep additionally recognises.
_AUTH_CALL_RE = re.compile(
    r"(?:get_current_user|get_admin_user|get_admin_access|"
    r"require_admin_permission|require_admin|require_permission|require_auth|"
    r"check_admin_permission|check_host_restriction|check_permission|"
    r"has_permission|verify_token|_?authenticate|verify_signature|"
    r"_require_[a-z0-9_]+|"
    r"request\.state\.\w*(?:user|admin|auth|token|scope))",
    re.IGNORECASE,
)


def _is_guard_call_name(name: str) -> bool:
    """Whether a called name actually denotes an auth gate.

    Full-name matching is deliberate: a response-model constructor such as
    ``PasskeyAuthenticateOptionsResponse`` contains the substring
    ``Authenticate`` but performs no authorization.
    """
    return bool(name and _AUTH_CALL_RE.fullmatch(name))


def _called_names(tree: ast.AST) -> set[str]:
    """Every call name in an endpoint AST, incl. dotted attribute forms.

    A bare ``ast.Attribute`` yields its terminal attr (e.g. ``check_permission``
    from ``self.check_permission``); a dotted form such as ``request.state.user``
    is included too so the ``request\\.state\\.`` guard alternative can match.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            out.add(func.id)
        elif isinstance(func, ast.Attribute):
            out.add(func.attr)
            parts: list[str] = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                out.add(".".join(reversed(parts)))
    return out

# Mutating routes that are intentionally reachable without a session token.
# Every entry states *why* it is public and must still be an unprotected route
# for the staleness check to accept it.
PUBLIC_MUTATING_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/auth/login"): "password/credential exchange; mints the token",
    ("POST", "/auth/2fa"): "second-factor step of a pending login",
    ("POST", "/auth/register"): "account creation for anonymous callers",
    ("POST", "/auth/oauth/{provider}/callback"): "OAuth provider callback",
    (
        "POST",
        "/auth/passkeys/authenticate",
    ): "passkey assertion is the credential",
    (
        "POST",
        "/auth/passkeys/options/authenticate",
    ): "pre-auth challenge issuance keyed by username; rate-limited, no session",
    ("POST", "/auth/password-reset/request"): "anonymous reset request",
    ("POST", "/auth/password-reset/confirm"): "reset token in body",
    ("POST", "/auth/resend-verification"): "anonymous verification resend",
    ("POST", "/auth/verify-email"): "verification token in body",
    (
        "POST",
        "/applications/interactions/{interaction_token}/callback",
    ): "interaction token in path authenticates the callback",
    ("POST", "/webhooks/{webhook_id}/{token}"): "webhook token in path",
    ("POST", "/telemetry/csp-report"): "anonymous CSP violation sink",
    ("POST", "/telemetry/response-times"): "anonymous timing telemetry sink",
    ("POST", "/version/negotiate"): "pre-auth version negotiation handshake",
}


def _flatten(router, prefix="", inherited=()):
    """Yield ``(path, route, deps)`` for every concrete route in the tree."""
    for route in router.routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            sub_deps = inherited + tuple(getattr(ctx, "dependencies", ()) or ())
            sub_deps += tuple(getattr(original, "dependencies", ()) or ())
            yield from _flatten(original, sub_prefix, sub_deps)
        elif hasattr(route, "methods") and hasattr(route, "path"):
            yield prefix + route.path, route, inherited


def _dependency_names(route) -> list[str]:
    out: list[str] = []

    def walk(dependant):
        for sub in getattr(dependant, "dependencies", ()) or ():
            out.append(getattr(sub.call, "__name__", repr(sub.call)))
            walk(sub)

    walk(getattr(route, "dependant", None))
    return out


def _endpoint_source(route) -> str:
    try:
        return inspect.getsource(route.endpoint) or ""
    except (TypeError, OSError):
        return ""


def _in_handler_guard(route) -> bool:
    """Whether the endpoint itself calls an auth guard.

    Parse the endpoint source and fullmatch each *called name* against the
    anchored vocabulary. Searching the raw source text let an incidental
    "authenticate" substring in a response-model name, cache-key builder or
    route path satisfy the invariant without any actual auth check.
    """
    source = _endpoint_source(route)
    if not source:
        return False
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, TypeError, ValueError):
        return False
    return any(_is_guard_call_name(name) for name in _called_names(tree))


def _is_protected(path, route, inherited) -> bool:
    deps = list(inherited) + _dependency_names(route)
    for dep in deps:
        name = getattr(dep, "__name__", repr(dep)).lower()
        if any(token in name for token in _AUTH_DEP_TOKENS):
            return True
    return _in_handler_guard(route)


def _mutating_routes():
    routes = []
    for path, route, inherited in _flatten(create_api_router()):
        for method in getattr(route, "methods", ()) or ():
            if method in _MUTATING:
                routes.append((method, path, _is_protected(path, route, inherited)))
    return routes


def test_route_discovery_is_not_vacuous():
    routes = _mutating_routes()
    assert len(routes) > 200, f"only discovered {len(routes)} mutating routes"
    # A duplicate method+path means two handlers shadow each other; auth on
    # one of them is not a guarantee for the other.
    keys = [(m, p) for m, p, _ in routes]
    assert len(keys) == len(set(keys)), "duplicate mutating method/path pairs"


def test_every_mutating_route_is_authenticated_or_justified_public():
    offenders = []
    for method, path, protected in _mutating_routes():
        if protected or (method, path) in PUBLIC_MUTATING_ROUTES:
            continue
        offenders.append(f"{method} {path}")
    assert not offenders, (
        "State-changing routes without an auth dependency or permission "
        "guard:\n  " + "\n  ".join(sorted(offenders))
    )


def test_public_route_allowlist_has_no_stale_entries():
    present = {(m, p) for m, p, protected in _mutating_routes() if not protected}
    stale = sorted(set(PUBLIC_MUTATING_ROUTES) - present)
    assert not stale, (
        "These public-route entries are now guarded or no longer exist; "
        "remove them from PUBLIC_MUTATING_ROUTES:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in stale)
    )


def test_public_allowlist_entries_are_all_public_flows():
    # Guard against a lazy ``("*", path)``-style catch-all being added to the
    # allowlist: each entry must name a concrete method and path.
    for method, path in PUBLIC_MUTATING_ROUTES:
        assert method in _MUTATING
        assert path.startswith("/")
        assert "*" not in path


def test_guard_call_name_matcher_is_not_vacuous():
    """Lookalike names must not be accepted as auth guards.

    ``PasskeyAuthenticateOptionsResponse`` is a response-model constructor and
    ``authenticated_cache_key`` a cache-key builder; an unanchored search
    accepted both via their ``authenticate`` substring, letting unguarded
    routes pass vacuously. Real guard names must still be accepted.
    """
    for lookalike in (
        "PasskeyAuthenticateOptionsResponse",
        "authenticated_cache_key",
        "is_authenticated_self_test",
        "require_https_url",
        "require_channel_snowflake",
        "verify_passkey_authentication",
    ):
        assert not _is_guard_call_name(lookalike), lookalike
    for guard in (
        "get_current_user",
        "get_admin_user",
        "_authenticate",
        "verify_token",
        "require_admin_permission",
        "check_host_restriction",
        "_require_channel_access",
        "check_permission",
        "has_permission",
    ):
        assert _is_guard_call_name(guard), guard


def test_response_model_only_handler_is_flagged_unguarded():
    """End-to-end: a handler whose sole auth-word call is a response model.

    The synthetic handler builds a ``PasskeyAuthenticateOptionsResponse`` but
    has no auth dependency or guard call. The in-handler scan must return False
    so a new such route fails closed unless explicitly allowlisted as public.
    """
    source = textwrap.dedent(
        """
        @router.post("/options/authenticate")
        async def options(body: str):
            return PasskeyAuthenticateOptionsResponse(
                challenge_id="x", options={}
            )
        """
    )
    tree = ast.parse(source)
    assert not any(_is_guard_call_name(n) for n in _called_names(tree))


def test_live_in_handler_guard_is_still_detected():
    """A genuine in-handler guard (incl. dotted/underscore forms) still counts."""
    source = textwrap.dedent(
        """
        async def handler(request: Request):
            check_host_restriction(request)
            _authenticate(request)
            return request.state.user
        """
    )
    tree = ast.parse(source)
    names = _called_names(tree)
    assert any(_is_guard_call_name(n) for n in names), names
