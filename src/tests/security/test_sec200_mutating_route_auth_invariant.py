"""SEC200 invariant: every state-changing route has an auth gate.

This is an independent, whole-tree AST lock on the mutating-route auth
invariant. It reads route modules directly instead of importing the app, so it
needs no config, database or fixture ordering and cannot flake. It fails the
moment a new ``@router.post/put/patch/delete`` handler is added without either
an auth-flavoured ``Depends(...)``, an explicit ``dependencies=[...]`` auth, or
an in-handler permission/signature guard.

The public set below is a ratchet keyed by source file + HTTP method + raw
decorator path, so two routes sharing ``POST /login`` cannot excuse each other.
Keep the justifications concrete: an entry is only acceptable when the route
authenticates the caller by some other means (a token in the path/body, a
credential exchange, an anonymous telemetry sink, etc.).
"""

import ast
import os
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})

# A dependency counts when its callable name looks like an auth principal or
# scope. Deliberately broad: a helper that renames ``get_current_user`` to
# ``load_principal`` still reads as auth.
AUTH_TOKENS = (
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

# An in-handler guard also protects a route even if no Depends() is present.
# The match is anchored to the WHOLE called name (via ``fullmatch`` below): an
# unanchored substring search let ``PasskeyAuthenticateOptionsResponse(...)``
# (a Pydantic response-model constructor) and ``authenticated_cache_key(...)``
# (a cache-key builder) masquerade as authentication guards. That made this
# invariant vacuous for ``POST /passkeys/options/authenticate`` -- a genuinely
# public pre-auth route with no auth check was reported as protected.
GUARD_CALL_RE = re.compile(
    r"(?:get_current_user|get_admin_user|get_admin_access|"
    r"require_admin_permission|require_admin|require_permission|require_auth|"
    r"check_admin_permission|check_permission|has_permission|"
    r"verify_token|_?authenticate|verify_signature|"
    r"_require_[a-z0-9_]+|"
    r"request\.state\.\w*(?:user|admin|auth|token|scope))",
    re.IGNORECASE,
)


def _is_guard_call_name(name: str) -> bool:
    """Whether a called function name actually denotes an auth gate.

    Full-name matching is deliberate: a response-model constructor such as
    ``PasskeyAuthenticateOptionsResponse`` contains the substring
    ``Authenticate`` but performs no authorization. See the vacuity note on
    ``GUARD_CALL_RE`` above.
    """
    return bool(name and GUARD_CALL_RE.fullmatch(name))

# Mutating routes intentionally reachable without a session token.
# (relative source file, HTTP method, raw decorator path) -> justification.
PUBLIC_MUTATING_ROUTES = {
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/login",
    ): "admin password exchange; mints the admin token",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/verify-otp",
    ): "second-factor step of a pending admin login",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/logout",
    ): "clears the caller's own admin session",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/auth/change-password",
    ): "authenticated by the caller's current password",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/auth/2fa/begin-setup",
    ): "pending admin session sets up its own 2FA",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/auth/2fa/disable",
    ): "pending admin session disables its own 2FA",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/auth/2fa/regenerate-backup-codes",
    ): "pending admin session regenerates its own codes",
    (
        "src/api/routes/applications.py",
        "post",
        "/interactions/{interaction_token}/callback",
    ): "interaction token in path authenticates the callback",
    (
        "src/api/routes/auth/email_verification.py",
        "post",
        "/verify-email",
    ): "verification token in body",
    (
        "src/api/routes/auth/email_verification.py",
        "post",
        "/resend-verification",
    ): "anonymous verification resend",
    (
        "src/api/routes/auth/login.py",
        "post",
        "/login",
    ): "password/credential exchange; mints the token",
    (
        "src/api/routes/auth/oauth.py",
        "post",
        "/oauth/{provider}/callback",
    ): "OAuth provider callback",
    (
        "src/api/routes/auth/passkeys.py",
        "post",
        "/passkeys/authenticate",
    ): "passkey assertion is the credential",
    (
        "src/api/routes/auth/passkeys.py",
        "post",
        "/passkeys/options/authenticate",
    ): "pre-auth challenge issuance keyed by username; rate-limited, no session",
    (
        "src/api/routes/auth/password.py",
        "post",
        "/password-reset/request",
    ): "anonymous reset request",
    (
        "src/api/routes/auth/password.py",
        "post",
        "/password-reset/confirm",
    ): "reset token in body",
    (
        "src/api/routes/auth/register.py",
        "post",
        "/register",
    ): "account creation for anonymous callers",
    (
        "src/api/routes/auth/two_factor.py",
        "post",
        "/2fa",
    ): "second-factor step of a pending login",
    (
        "src/api/routes/telemetry.py",
        "post",
        "/response-times",
    ): "anonymous timing telemetry sink",
    (
        "src/api/routes/telemetry.py",
        "post",
        "/csp-report",
    ): "anonymous CSP violation sink",
    (
        "src/api/routes/version.py",
        "post",
        "/version/negotiate",
    ): "pre-auth version negotiation handshake",
    (
        "src/api/routes/webhooks.py",
        "post",
        "/{webhook_id}/{token}",
    ): "webhook token in path",
}


def _dependency_names(expr):
    out = []
    if expr is None:
        return out
    for node in ast.walk(expr):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Depends"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                out.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                out.append(arg.attr)
    return out


def _looks_like_auth(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in AUTH_TOKENS)


def _is_protected(fn: ast.AST) -> bool:
    args = fn.args
    defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    for default in defaults:
        if any(_looks_like_auth(name) for name in _dependency_names(default)):
            return True
    for decorator in fn.decorator_list:
        if isinstance(decorator, ast.Call):
            for kw in decorator.keywords:
                if kw.arg == "dependencies":
                    if any(_looks_like_auth(n) for n in _dependency_names(kw.value)):
                        return True
    # Only a guard that executes when the route runs counts. ``ast.walk``
    # descends into nested functions/lambdas, where a guard call is dead until
    # that callable is invoked; it must not mark the route as protected.
    return bool(_own_body_guard_calls(fn))


def _own_body_guard_calls(fn: ast.AST):
    """Guard-like calls that execute on this function's own path.

    Descend through statement-level nesting (if/for/with/try) but skip nested
    function and lambda bodies so dead evidence cannot silence the invariant.
    """
    hits = []
    stack = [fn]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if _is_guard_call_name(name):
                    hits.append(child)
            stack.append(child)
    return hits


def _mutating_routes():
    for directory, dirs, files in os.walk(ROUTES):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(directory) / filename
            relative = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(
                        decorator.func, ast.Attribute
                    ):
                        continue
                    method = decorator.func.attr
                    if method not in MUTATING:
                        continue
                    raw_path = ""
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        raw_path = decorator.args[0].value
                    yield (relative, method, raw_path), _is_protected(node)


_ROUTES = list(_mutating_routes())


def test_route_scan_is_not_vacuous():
    assert len(_ROUTES) > 150, f"only discovered {len(_ROUTES)} mutating handlers"


def test_every_mutating_route_is_protected_or_justified_public():
    offenders = [
        f"{rel}: {method.upper()} {path}"
        for (rel, method, path), protected in _ROUTES
        if not protected and (rel, method, path) not in PUBLIC_MUTATING_ROUTES
    ]
    assert not offenders, (
        "Mutating route handlers with no auth dependency or permission "
        "guard:\n  " + "\n  ".join(sorted(offenders))
    )


def test_public_route_allowlist_has_no_stale_entries():
    present = {key for key, protected in _ROUTES if not protected}
    stale = sorted(set(PUBLIC_MUTATING_ROUTES) - present)
    assert not stale, (
        "These public-route entries are now guarded or no longer exist; "
        "remove them from PUBLIC_MUTATING_ROUTES:\n  "
        + "\n  ".join(f"{rel}: {m.upper()} {p}" for rel, m, p in stale)
    )


def test_public_allowlist_entries_are_concrete():
    for rel, method, path in PUBLIC_MUTATING_ROUTES:
        assert method in MUTATING
        assert path.startswith("/")
        assert "*" not in path
        assert rel.startswith("src/api/routes/")


def test_guard_name_matcher_rejects_response_model_lookalikes():
    """Non-vacuity: names that merely contain an auth word are not guards.

    ``PasskeyAuthenticateOptionsResponse(...)`` is a Pydantic response-model
    constructor and ``authenticated_cache_key(...)`` a cache-key builder. An
    unanchored substring search accepted both as authentication, which made
    the public pre-auth passkey-options route pass this invariant with no auth
    check at all. Genuine guard names must still be recognised.
    """
    for lookalike in (
        "PasskeyAuthenticateOptionsResponse",
        "authenticated_cache_key",
        "is_authenticated_self_test",
        "PasskeyRegistrationResponse",
        "require_https_url",
        "require_channel_snowflake",
    ):
        assert not _is_guard_call_name(lookalike), lookalike
    for guard in (
        "get_current_user",
        "get_admin_user",
        "_authenticate",
        "verify_token",
        "require_admin_permission",
        "_require_channel_access",
        "check_permission",
        "has_permission",
    ):
        assert _is_guard_call_name(guard), guard


def test_response_model_only_handler_is_flagged_unguarded():
    """End-to-end: a handler whose sole auth-word call is a response model.

    This synthetic handler mirrors the real vacuity: it builds a
    ``PasskeyAuthenticateOptionsResponse`` but has no auth dependency or guard
    call. ``_is_protected`` must return False so a new such route fails closed
    unless it is explicitly allowlisted as public.
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
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert _is_protected(fn) is False


def test_dead_nested_guard_does_not_protect_route():
    """A guard inside a never-called nested callable must not gate a route.

    The in-handler guard scan used ``ast.walk(fn)``, which descends into nested
    functions and lambdas. Merely *defining* a helper/lambda that calls a guard
    does not execute it, so dead evidence could silence this invariant. A live
    guard inside statement-level nesting must still count.
    """
    sources = (
        textwrap.dedent(
            """
            @router.post("/dead-nested")
            async def dead_nested(request: Request):
                def never_called():
                    check_admin_permission(request)
                return None
            """
        ),
        textwrap.dedent(
            """
            @router.post("/dead-lambda")
            async def dead_lambda(request: Request):
                _ = lambda: verify_token(request)
                return None
            """
        ),
        textwrap.dedent(
            """
            @router.post("/live")
            async def live(request: Request):
                if request.state.foo:
                    check_admin_permission(request)
                return None
            """
        ),
    )
    for source, expected in zip(sources, (False, False, True)):
        tree = ast.parse(source)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        assert _is_protected(fn) is expected, source
