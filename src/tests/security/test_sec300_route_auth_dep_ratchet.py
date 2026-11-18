"""SEC300 ratchet: every state-changing route handler carries an auth gate.

This is an independent, whole-tree AST lock on the mutating-route auth
invariant. It is deliberately separate from the SEC200 suite: it uses a broader
in-handler guard detector (so a handler that calls ``require_*`` / ``get_current_*``
/ ``assert_*auth*`` directly is recognised without a ``Depends(...)``), and its
public allowlist is keyed by (relative source file, HTTP method, raw path).

The suite scans source files rather than importing the app, so it needs no
config, database or fixture ordering and cannot flake. A new
``@router.post/put/patch/delete`` handler fails the build the moment it appears
without an auth-flavoured dependency, an in-handler permission guard, or a
concrete entry in ``PUBLIC_MUTATING_ROUTES`` with a real justification.

Self-tests below prove the detector catches the unprotected shape and
recognises every protected shape before it is trusted over the tree.
"""

import ast
import os
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})

# A Depends() callable counts as auth when its name reads like a principal or a
# scope. Deliberately broad: a project helper may rename get_current_user to
# load_principal without weakening the check.
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

# In-handler guards: an explicit permission check is as real a gate as a
# dependency, and admin routes use this shape extensively.
GUARD_RE = re.compile(
    r"(?:require\w*|get_current\w*|get_admin\w*|check_admin\w*|"
    r"check_permission\w*|has_permission\w*|verify_token\w*|"
    r"validate_token\w*|authenticate\w*|verify_signature\w*|"
    r"ensure_auth\w*|assert_auth\w*|"
    r"request\.state\.\w*(?:user|admin|auth|token|scope))",
    re.IGNORECASE,
)

# Mutating routes intentionally reachable without a session token. Each entry
# is (relative source file, HTTP method, raw decorator path) -> justification;
# two routes sharing POST /login cannot excuse one another.
PUBLIC_MUTATING_ROUTES = {
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/logout",
    ): "clears the caller's own admin session",
    (
        "src/api/routes/admin/auth.py",
        "post",
        "/verify-otp",
    ): "second-factor step of a pending admin login",
    (
        "src/api/routes/applications.py",
        "post",
        "/interactions/{interaction_token}/callback",
    ): "interaction token in path authenticates the callback",
    (
        "src/api/routes/auth/email_verification.py",
        "post",
        "/resend-verification",
    ): "anonymous verification resend",
    (
        "src/api/routes/auth/email_verification.py",
        "post",
        "/verify-email",
    ): "verification token in body",
    (
        "src/api/routes/auth/login.py",
        "post",
        "/login",
    ): "password exchange; mints the session token",
    (
        "src/api/routes/auth/passkeys.py",
        "post",
        "/passkeys/authenticate",
    ): "passkey assertion is the credential",
    (
        "src/api/routes/auth/password.py",
        "post",
        "/password-reset/confirm",
    ): "reset token in body",
    (
        "src/api/routes/auth/password.py",
        "post",
        "/password-reset/request",
    ): "anonymous reset request",
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
        "/csp-report",
    ): "anonymous CSP violation sink",
    (
        "src/api/routes/telemetry.py",
        "post",
        "/response-times",
    ): "anonymous timing telemetry sink",
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
    """Yield callable names wrapped in ``Depends(...)`` inside ``expr``."""
    if expr is None:
        return
    for node in ast.walk(expr):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Depends"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                yield arg.id
            elif isinstance(arg, ast.Attribute):
                yield arg.attr


def _looks_like_auth(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in AUTH_TOKENS)


def _has_auth_dependency(fn) -> bool:
    args = fn.args
    defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    for default in defaults:
        if any(_looks_like_auth(name) for name in _dependency_names(default)):
            return True
    for decorator in fn.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "dependencies" and any(
                    _looks_like_auth(name) for name in _dependency_names(keyword.value)
                ):
                    return True
    return False


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_in_handler_guard(fn) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and GUARD_RE.search(_call_name(node)):
            return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "request"
            and GUARD_RE.search(node.attr)
        ):
            return True
    return False


def _is_protected(fn) -> bool:
    return _has_auth_dependency(fn) or _has_in_handler_guard(fn)


def _routes_in_tree(tree, relative):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in MUTATING
            ):
                continue
            method = decorator.func.attr
            raw_path = ""
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                raw_path = decorator.args[0].value
            yield (relative, method, raw_path), _is_protected(node)


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
            yield from _routes_in_tree(tree, relative)


_ROUTES = list(_mutating_routes())
_ROUTE_KEYS = [key for key, _ in _ROUTES]


def test_detector_recognises_protected_and_unprotected_shapes():
    """Self-test: trust the detector only after it passes known shapes."""
    source = """
@router.post("/dep")
def by_dep(user=Depends(get_current_user)):
    return None

@router.patch("/guard")
def by_guard(request: Request):
    require_admin_permission(request, "x")
    return None

@router.put("/bare")
def bare(request: Request):
    return None

@router.get("/read")
def read_only(request: Request):
    return None
"""
    # The decorator-level dependencies= form is exercised separately because
    # it is the other supported dependency shape.
    decorator_deps = """
@router.post("/deps", dependencies=[Depends(require_auth)])
def by_decorator():
    return None
"""
    tree = ast.parse(source + decorator_deps)
    routes = {key: protected for key, protected in _routes_in_tree(tree, "sample.py")}
    assert routes[("sample.py", "post", "/dep")] is True
    assert routes[("sample.py", "post", "/deps")] is True
    assert routes[("sample.py", "patch", "/guard")] is True
    assert routes[("sample.py", "put", "/bare")] is False
    assert ("sample.py", "get", "/read") not in routes


def test_route_scan_is_not_vacuous():
    assert len(_ROUTES) > 200, f"only discovered {len(_ROUTES)} mutating handlers"
    assert any(rel.endswith("admin/security.py") for rel, _, _ in _ROUTE_KEYS)


def test_every_mutating_route_is_gated_or_justified_public():
    offenders = [
        f"{rel}: {method.upper()} {path}"
        for (rel, method, path), protected in _ROUTES
        if not protected and (rel, method, path) not in PUBLIC_MUTATING_ROUTES
    ]
    assert not offenders, (
        "Mutating route handlers with no auth dependency, permission guard or "
        "public justification:\n  " + "\n  ".join(sorted(offenders))
    )


def test_public_allowlist_is_live_and_concrete():
    unprotected = {key for key, protected in _ROUTES if not protected}
    missing = sorted(set(PUBLIC_MUTATING_ROUTES) - unprotected)
    assert not missing, (
        "Public-route entries no longer describe an unprotected handler; "
        "remove or update them:\n  "
        + "\n  ".join(f"{rel}: {m.upper()} {p}" for rel, m, p in missing)
    )
    for rel, method, path in PUBLIC_MUTATING_ROUTES:
        assert method in MUTATING
        assert path.startswith("/")
        assert "*" not in path
        assert rel.startswith("src/api/routes/")
        assert PUBLIC_MUTATING_ROUTES[(rel, method, path)].strip()
