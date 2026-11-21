"""Static, whole-tree guard: mutating routes need auth or a documented reason.

This is a source-level (AST) lock on the same invariant that
``test_security_d2_route_auth`` checks against the built route tree. Because it
reads route modules directly it needs no app import, no config and no DB, so it
cannot flake on fixture ordering -- and it fails the moment a new
``@router.post/put/patch/delete`` handler is added without an auth dependency
or an in-handler permission guard.

A dependency counts when its callable name looks like an auth principal/scope
(``get_current_user``, ``require_admin_permission``, ``...``); a handler counts
when it calls one of the known guard functions or ``_require_*`` helpers. The
public set below is a ratchet keyed by source file + raw decorator path, so two
routes that share ``POST /login`` cannot excuse each other.
"""

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})

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

GUARD_CALL_RE = re.compile(
    r"(?:get_current_user|get_admin_user|get_admin_access|"
    r"require_admin_permission|require_admin|require_permission|require_auth|"
    r"check_admin_permission|check_permission|has_permission|verify_token|"
    r"authenticate|verify_signature|_require_\w+|"
    r"request\.state\.\w*(?:user|admin|auth|token|scope))",
    re.IGNORECASE,
)

# Mutating routes that are intentionally reachable without a session token.
# Key: (relative source file, HTTP method, raw decorator path) -> justification.
PUBLIC_MUTATING_ROUTES: dict[tuple[str, str, str], str] = {
    (
        "src/api/routes/applications.py",
        "post",
        "/interactions/{interaction_token}/callback",
    ): "interaction token in path authenticates the callback",
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


def _own_body_guard_calls(fn: ast.AST):
    """Guard-like calls that actually execute on this handler's path.

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that calls
    a guard does not run it. Counting such a call as protection lets an
    unauthenticated mutating route silence this invariant with dead evidence
    (the same vacuity fixed in the sibling SEC200 route guards).
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
                if GUARD_CALL_RE.search(name):
                    hits.append(child)
            stack.append(child)
    return hits


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
    # Only a guard that runs when the route runs counts. A guard call inside a
    # nested callable is dead until that callable is invoked, so it must not
    # mark the route as protected.
    return bool(_own_body_guard_calls(fn))


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


def test_dead_nested_guard_does_not_protect_mutating_route():
    """A guard in a never-invoked nested callable must not gate the route.

    Regression: the in-handler evidence scan used ``ast.walk(fn)``, which
    descends into nested functions and lambdas. Merely *defining* a helper or
    lambda that calls a guard did not execute it, so an unauthenticated mutating
    route could silence this invariant with dead evidence. A live in-handler
    guard must still count.
    """
    source = """
@router.post("/dead-nested")
async def dead_nested(request: Request):
    def never_called():
        check_admin_permission(request)
    return None

@router.put("/dead-lambda")
async def dead_lambda(request: Request):
    _ = lambda: verify_token(request)
    return None

@router.delete("/live")
async def live(request: Request):
    check_admin_permission(request)
    return None
"""
    fns = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert _is_protected(fns["dead_nested"]) is False, (
        "dead guard in a nested function must not protect the route"
    )
    assert _is_protected(fns["dead_lambda"]) is False, (
        "dead guard in a lambda must not protect the route"
    )
    assert _is_protected(fns["live"]) is True, (
        "a live in-handler guard must still protect the route"
    )


def test_route_scan_is_not_vacuous():
    assert len(_ROUTES) > 150, f"only discovered {len(_ROUTES)} mutating route handlers"


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
