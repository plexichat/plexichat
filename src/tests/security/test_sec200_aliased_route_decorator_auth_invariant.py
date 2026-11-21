"""SEC200 invariant: mutating routes registered through an aliased decorator.

The existing sweeps all require the decorator function to be an ``ast.Attribute``:

* ``test_sec200_mutating_route_auth_invariant`` -- ``@router.post`` etc.
* ``test_sec200_generic_route_decorator_auth_invariant`` --
  ``@router.api_route(..., methods=[...])``.
* ``test_sec200_dynamic_add_api_route_auth_invariant`` -- runtime
  ``router.add_api_route(...)`` calls.
* ``test_sec300_route_auth_dep_ratchet`` -- same attribute shape as the first.

FastAPI/Starlette route factories are ordinary bound methods, so they can be
aliased and then used as a *bare* decorator name::

    post = router.post

    @post("/admin/nuke")
    async def nuke(request: Request): ...

That decorator is an ``ast.Name``, not an ``ast.Attribute``, so all four sweeps
skip it. A state-changing handler registered that way is unauthenticated while
passing every current guard. This module closes that gap with an independent,
whole-tree AST scan of ``src/api/routes/**``. It imports no app, config or DB
and cannot flake.

Detection is intentionally fail-closed: any name bound (anywhere in the module)
to a route-factory attribute ``.post/.put/.patch/.delete/.api_route/.route`` is
tracked, and a bare factory name used as a decorator is treated as a mutating
registration even without an explicit alias. For generic factories an
unresolvable ``methods`` expression is counted as mutating rather than silently
skipped. Auth vocabulary mirrors the decorator sweep so the scans agree on what
counts as a guard.

Synthetic fixtures below prove the detector flags the unguarded alias shape and
recognises the guarded one, so the tree-wide assertion is not vacuous.
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
GENERIC_ROUTE_DECORATORS = frozenset({"api_route", "route"})
ROUTE_FACTORIES = MUTATING | GENERIC_ROUTE_DECORATORS
UNRESOLVED_METHOD = "<unresolved>"

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

# Mutating aliased-decorator registrations intentionally reachable without a
# session token. (relative source file, method, raw path) -> justification.
# Keep empty unless a genuinely public mutating route uses an alias; every
# entry needs a concrete justification.
PUBLIC_ALIASED_MUTATING_ROUTES = {}


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
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if GUARD_CALL_RE.search(name):
                return True
    return False


def _route_factory_aliases(tree):
    """Map bare names bound to a route-factory attribute within ``tree``.

    Covers ``post = router.post``, annotated assignments and walrus bindings,
    at module or nested scope. Collecting across the whole module means a
    decorator can use an alias defined in an outer scope and still be seen.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, ast.NamedExpr):
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Attribute) or value.attr not in ROUTE_FACTORIES:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = value.attr
    return aliases


def _methods_for_factory(factory, decorator):
    """Yield mutating methods registered by an aliased generic decorator.

    A missing ``methods`` kwarg is FastAPI's read-only GET default. A literal
    collection of strings is resolved exactly; an unresolvable expression
    yields the unresolved sentinel so the route fails closed (gets checked).
    """
    if factory in MUTATING:
        yield factory
        return
    methods_kw = next((kw for kw in decorator.keywords if kw.arg == "methods"), None)
    if methods_kw is None:
        return
    value = methods_kw.value
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                if elt.value.lower() in MUTATING:
                    yield elt.value.lower()
            elif not isinstance(elt, ast.Starred):
                yield UNRESOLVED_METHOD
    elif isinstance(value, ast.Constant) and isinstance(value.value, str):
        if value.value.lower() in MUTATING:
            yield value.value.lower()
    else:
        yield UNRESOLVED_METHOD


def _aliased_routes_in_tree(tree, relative):
    aliases = _route_factory_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            # The missed shape is a bare ast.Name decorator; attribute-shaped
            # decorators belong to the other sweeps and are intentionally skipped.
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Name
            ):
                continue
            name = decorator.func.id
            factory = aliases.get(name)
            if factory is None and name in ROUTE_FACTORIES:
                # Fail closed: a bare factory name with no resolvable alias is
                # still a route registration.
                factory = name
            if factory is None:
                continue
            raw_path = ""
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                raw_path = decorator.args[0].value
            for method in _methods_for_factory(factory, decorator):
                yield (relative, method, raw_path), _is_protected(node)


def _aliased_mutating_routes():
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
            yield from _aliased_routes_in_tree(tree, relative)


_ROUTES = list(_aliased_mutating_routes())


def test_aliased_route_scan_detector_flags_unguarded_alias():
    """Non-vacuity: the detector flags an aliased unguarded mutation."""
    source = """
from fastapi import APIRouter, Request
router = APIRouter()
post = router.post

@post("/admin/nuke")
async def nuke(request: Request):
    return {"ok": True}

@post("/admin/safe")
async def safe(request: Request, current_user=Depends(get_current_user)):
    return {"ok": True}
"""
    found = dict(
        _aliased_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    key_bad = ("src/api/routes/synthetic.py", "post", "/admin/nuke")
    key_good = ("src/api/routes/synthetic.py", "post", "/admin/safe")
    assert key_bad in found, "detector missed the aliased @post registration"
    assert found[key_bad] is False, "unguarded aliased mutation was marked protected"
    assert found[key_good] is True, "Depends(get_current_user) was not recognised"


def test_aliased_decorator_shapes_and_fail_closed():
    """Generic aliases, unresolved methods and non-route names are handled."""
    source = """
router = APIRouter()
mutate = router.api_route
delete = router.delete

@mutate("/bad", methods=["POST", "GET"])
async def bad(request: Request):
    return None

@delete("/gone", dependencies=[Depends(require_auth)])
async def gone(request: Request):
    return None

@mutate("/dynamic", methods=METHODS)
async def dynamic(request: Request):
    return None

@mutate("/read-only", methods=["GET"])
async def read_only(request: Request):
    return None

@dataclass
class NotARoute:
    pass
"""
    found = dict(_aliased_routes_in_tree(ast.parse(source), "sample.py"))
    assert found[("sample.py", "post", "/bad")] is False
    assert found[("sample.py", "delete", "/gone")] is True
    assert found[("sample.py", UNRESOLVED_METHOD, "/dynamic")] is False
    assert ("sample.py", "get", "/read-only") not in found
    assert ("sample.py", "delete", "/read-only") not in found
    # A bare non-route decorator must never be reported as a route.
    assert not any(path == "" and method in MUTATING for _, method, path in found)


def test_every_aliased_mutating_route_is_protected_or_justified_public():
    offenders = [
        f"{rel}: {method.upper()} {path}"
        for (rel, method, path), protected in _ROUTES
        if not protected and (rel, method, path) not in PUBLIC_ALIASED_MUTATING_ROUTES
    ]
    assert not offenders, (
        "Mutating routes registered via aliased/bare decorators with no auth "
        "dependency or permission guard:\n  " + "\n  ".join(sorted(offenders))
    )


def test_public_allowlist_has_no_stale_entries():
    present = {key for key, protected in _ROUTES if not protected}
    stale = sorted(set(PUBLIC_ALIASED_MUTATING_ROUTES) - present)
    assert not stale, (
        "Public aliased-route entries are now guarded or no longer exist; "
        "remove them from PUBLIC_ALIASED_MUTATING_ROUTES:\n  "
        + "\n  ".join(f"{rel}: {m.upper()} {p}" for rel, m, p in stale)
    )
