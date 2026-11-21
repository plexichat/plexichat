"""SEC200 invariant: mutating generic route decorators are auth-gated.

The decorator sweep (``test_sec200_mutating_route_auth_invariant.py``) only
walks ``@router.post/put/patch/delete`` decorators. FastAPI/Starlette also
register state-changing routes through generic decorators whose HTTP method is
passed in a ``methods=[...]`` kwarg::

    @router.api_route("/x", methods=["POST"])
    @app.route("/x", methods=["DELETE"])

Those decorators are invisible to that sweep *and* to the ``add_api_route``
sweep, because their endpoint is bound by a decorator rather than a runtime
``add_api_route(...)`` call. An unauthenticated mutation registered that way
therefore slips through both invariants. This module closes that gap with an
independent, whole-tree AST scan of ``src/api/routes/**``. It imports no app,
config or DB and cannot flake.

For every generic route decorator that can register a mutating method we reuse
the decorator sweep's auth vocabulary: an auth-flavoured ``Depends(...)`` on the
handler (or on the decorator's ``dependencies=[...]``), or an in-handler
permission/signature guard. Every mutating registration must be guarded unless
explicitly allowlisted as a legitimately public credential-exchange route.

An unresolvable ``methods`` expression (a name, attribute or call) is counted as
mutating so the route is checked rather than silently skipped -- fail closed.
The detector is proven non-vacuous by a synthetic unguarded registration.
"""

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})
GENERIC_ROUTE_DECORATORS = frozenset({"api_route", "route"})
UNRESOLVED_METHOD = "<unresolved>"

# Mirrors the decorator sweep's auth vocabulary so the two scans agree on what
# counts as a guard. Deliberately broad: a renamed principal helper still reads
# as auth.
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

# Mutating generic-decorator registrations intentionally reachable without a
# session token. (relative source file, method, raw path) -> justification.
# Keep empty unless a genuinely public mutating route uses these decorators;
# every entry needs a concrete justification.
PUBLIC_GENERIC_MUTATING_ROUTES = {}


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
    """Guard-like calls that actually execute on this function's path.

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that calls
    a guard does not run it. Counting such a call as protection lets an
    unauthenticated mutating route silence this invariant with dead evidence
    (the same vacuity fixed in the cached-route completeness guard).
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
    # Only a guard that executes when the route runs counts. A guard call in a
    # nested callable is dead until that callable is invoked, so it must not
    # mark the route as protected.
    return bool(_own_body_guard_calls(fn))


def _decorator_mutating_methods(decorator):
    """Yield the mutating HTTP methods registered by a generic decorator.

    A missing ``methods`` kwarg is FastAPI's read-only GET default. A literal
    collection of strings is resolved exactly; a dynamic expression yields the
    unresolved sentinel so the route fails closed (gets checked) rather than
    being silently skipped.
    """
    if not isinstance(decorator, ast.Call) or not isinstance(
        decorator.func, ast.Attribute
    ):
        return
    if decorator.func.attr not in GENERIC_ROUTE_DECORATORS:
        return
    methods_kw = next((kw for kw in decorator.keywords if kw.arg == "methods"), None)
    if methods_kw is None:
        return
    value = methods_kw.value
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                lowered = elt.value.lower()
                if lowered in MUTATING:
                    yield lowered
            elif not isinstance(elt, ast.Starred):
                yield UNRESOLVED_METHOD
    elif isinstance(value, ast.Constant) and isinstance(value.value, str):
        lowered = value.value.lower()
        if lowered in MUTATING:
            yield lowered
    else:
        yield UNRESOLVED_METHOD


def _generic_routes_in_tree(tree, relative):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            raw_path = ""
            if isinstance(decorator, ast.Call) and decorator.args:
                if isinstance(decorator.args[0], ast.Constant):
                    raw_path = decorator.args[0].value
            for method in _decorator_mutating_methods(decorator):
                yield (relative, method, raw_path), _is_protected(node)


def _generic_mutating_routes():
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
            yield from _generic_routes_in_tree(tree, relative)


_ROUTES = list(_generic_mutating_routes())


def test_generic_route_scan_is_not_vacuous():
    """The detector flags a synthetic unguarded generic mutation."""
    source = """
@router.api_route("/bad", methods=["POST"])
async def bad(request: Request):
    return None

@app.route("/good", methods=["DELETE"])
async def good(request: Request, current_user=Depends(get_current_user)):
    return None
"""
    tree = ast.parse(source)
    found = dict(_generic_routes_in_tree(tree, "src/api/routes/synthetic.py"))
    assert found[("src/api/routes/synthetic.py", "post", "/bad")] is False, (
        "detector failed to flag an unguarded @router.api_route mutation"
    )
    assert found[("src/api/routes/synthetic.py", "delete", "/good")] is True, (
        "detector failed to recognise Depends(get_current_user) on a generic route"
    )


def test_dead_nested_guard_does_not_protect_generic_route():
    """A guard inside a never-called nested callable must NOT gate the route.

    Regression: the in-handler guard scan used ``ast.walk(fn)``, which descends
    into nested functions and lambdas. Merely *defining* a helper/lambda that
    calls a guard did not execute it, so an unauthenticated generic mutation
    could silence this invariant with dead evidence. A live in-handler guard
    must still count.
    """
    source = """
@router.api_route("/dead-nested", methods=["POST"])
async def dead_nested(request: Request):
    def never_called():
        check_admin_permission(request)
    return None

@router.api_route("/dead-lambda", methods=["POST"])
async def dead_lambda(request: Request):
    _ = lambda: verify_token(request)
    return None

@router.api_route("/live", methods=["POST"])
async def live(request: Request):
    check_admin_permission(request)
    return None
"""
    found = dict(
        _generic_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    assert found[("src/api/routes/synthetic.py", "post", "/dead-nested")] is False, (
        "dead guard in a nested function must not protect the route"
    )
    assert found[("src/api/routes/synthetic.py", "post", "/dead-lambda")] is False, (
        "dead guard in a lambda must not protect the route"
    )
    assert found[("src/api/routes/synthetic.py", "post", "/live")] is True, (
        "a live in-handler guard must still protect the route"
    )


def test_generic_decorator_shapes_and_fail_closed():
    """Generic decorators are resolved; read-only generic routes are skipped."""
    source = """
@router.api_route("/unguarded", methods=["POST", "GET"])
async def unguarded(request: Request):
    return None

@app.route("/mixed", methods=["GET", "PATCH"])
async def mixed(request: Request):
    return None

@router.api_route("/read-only", methods=["GET"])
async def read_only(request: Request):
    return None

@router.api_route("/dynamic", methods=METHODS)
async def dynamic(request: Request):
    return None
"""
    found = dict(_generic_routes_in_tree(ast.parse(source), "sample.py"))
    assert found[("sample.py", "post", "/unguarded")] is False
    assert found[("sample.py", "patch", "/mixed")] is False
    assert found[("sample.py", UNRESOLVED_METHOD, "/dynamic")] is False
    assert ("sample.py", "get", "/read-only") not in found
    assert ("sample.py", "get", "/unguarded") not in found


def test_every_mutating_generic_route_is_protected_or_justified_public():
    offenders = [
        f"{rel}: {method.upper()} {path}"
        for (rel, method, path), protected in _ROUTES
        if not protected and (rel, method, path) not in PUBLIC_GENERIC_MUTATING_ROUTES
    ]
    assert not offenders, (
        "Mutating routes registered via generic decorators "
        "(@router.api_route / @app.route) with no auth dependency or permission "
        "guard:\n  " + "\n  ".join(sorted(offenders))
    )


def test_public_generic_allowlist_has_no_stale_entries():
    present = {key for key, protected in _ROUTES if not protected}
    stale = sorted(set(PUBLIC_GENERIC_MUTATING_ROUTES) - present)
    assert not stale, (
        "Public generic-route entries no longer describe an unprotected "
        "handler; remove them from PUBLIC_GENERIC_MUTATING_ROUTES:\n  "
        + "\n  ".join(f"{rel}: {m.upper()} {p}" for rel, m, p in stale)
    )
