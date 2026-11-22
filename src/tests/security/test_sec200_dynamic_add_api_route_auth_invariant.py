"""SEC200 invariant: dynamically registered mutating routes are auth-gated.

The decorator-based sweep lives in
``test_sec200_mutating_route_auth_invariant.py``. That sweep does not see routes
registered at runtime with ``router.add_api_route(...)`` or Starlette's low-level
``router.add_route(...)``: those bind the endpoint by reference, so no
``@router.post``/``@router.delete`` decorator is present for the AST to find.
This module closes that gap with an independent, whole-tree AST scan of
``src/api/routes/**``. It imports no app, config or DB and cannot flake.

Both registration forms are inventoried, including local aliases of the
registrar and of the ``methods`` collection (a module-level literal list such as
``VERBS = ["POST"]`` passed as ``methods=VERBS``). An unresolvable ``methods``
expression is treated as potentially mutating rather than silently dropped.
``add_websocket_route`` is deliberately out of scope: WebSocket handlers are
gated by their own opcode handshake, not by HTTP route dependencies.

For each registration we resolve the endpoint function by name within the same
module and reuse the same auth heuristics as the decorator sweep (auth flavoured
``Depends(...)`` on the endpoint or, for ``add_api_route``, on the registration's
``dependencies=[...]``, or an in-handler permission/signature guard). Every
mutating registration must be guarded unless explicitly allowlisted as a
legitimately public credential-exchange route.

Synthetic fixtures feed the detector an unguarded ``add_api_route`` and an
unguarded ``add_route`` registration and assert both are flagged, while guarded
and read-only registrations pass, so the tree-wide assertion is not vacuous.
"""

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})

# Runtime registration forms that bind an endpoint without a decorator.
# ``add_api_route`` is FastAPI's high-level form (it also accepts
# ``dependencies=[...]``); ``add_route`` is Starlette's low-level form and has
# no registration-level dependency hook, so auth must live on the endpoint.
# Both are inventoried so a future mutating route registered through either
# form fails closed. ``add_websocket_route`` is deliberately NOT here: WS
# handlers are authenticated by their own opcode handshake, not HTTP deps.
DYNAMIC_REGISTRARS = ("add_api_route", "add_route")

# A ``methods`` expression that cannot be resolved from source may still hide a
# mutating verb, so it is inventoried as a potentially mutating registration
# instead of being silently dropped (mirrors the audit D sweep's fail-closed
# policy for unresolved dynamic methods).
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

# Matched against the WHOLE called name via ``fullmatch`` below. An unanchored
# substring search let ``PasskeyAuthenticateOptionsResponse(...)`` (a response
# model) count as auth -- keep the vocabulary anchored so the sweep cannot be
# satisfied by an incidental "authenticate" substring in a symbol name.
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
    """Whether a called function name actually denotes an auth gate."""
    return bool(name and GUARD_CALL_RE.fullmatch(name))

# Mutating ``add_api_route`` registrations intentionally reachable without a
# session token. (relative source file, HTTP method, raw path) -> justification.
# Keep empty unless a credential-exchange / token-in-path route is registered
# dynamically; every entry needs a concrete justification.
PUBLIC_DYNAMIC_MUTATING_ROUTES = {}


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


def _canonical_names(tree):
    """Resolve local aliases of every dynamic registrar to its canonical name.

    ``add = router.add_api_route`` (or ``add = router.add_route``) followed by
    ``add(...)`` is a plain ``ast.Name`` call, not the ``ast.Attribute`` this
    detector keys on. Without resolution such a state-changing registration is
    never inventoried and every invariant below passes vacuously for it, even
    when its endpoint carries no auth -- the same name-resolution fail-open
    fixed for the fan-out, cached-route and admin-destructive guards.

    Resolution runs to a fixpoint so chained aliases
    (``add = router.add_route; wire = add``) collapse too. A bare call spelled
    with a registrar name but no resolvable alias is still treated as a
    registration, so over-resolution can only *add* sites to the inventory,
    never drop one -- the guard fails closed.
    """
    aliases = {}
    nodes = list(ast.walk(tree))

    def resolve(name):
        seen = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    def canonical_of(value):
        if isinstance(value, ast.Attribute) and value.attr in DYNAMIC_REGISTRARS:
            return value.attr
        if isinstance(value, ast.Name):
            resolved = resolve(value.id)
            if resolved in DYNAMIC_REGISTRARS:
                return resolved
        return None

    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            pairs = []
            if isinstance(node, ast.ImportFrom):
                pairs = [
                    (a.asname or a.name, a.name)
                    for a in node.names
                    if a.name in DYNAMIC_REGISTRARS
                ]
            elif isinstance(node, ast.Assign):
                canonical = canonical_of(node.value)
                if canonical is not None:
                    pairs = [
                        (t.id, canonical)
                        for t in node.targets
                        if isinstance(t, ast.Name)
                    ]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                canonical = canonical_of(node.value)
                if canonical is not None and isinstance(node.target, ast.Name):
                    pairs = [(node.target.id, canonical)]
            for local, canonical in pairs:
                if aliases.get(local) != canonical:
                    aliases[local] = canonical
                    changed = True
        if not changed:
            break
    return aliases


def _method_aliases(tree):
    """Map local names to the expression they are bound to, for method resolution.

    A registration may pass ``methods=VERBS`` where ``VERBS = ["POST"]`` is
    defined elsewhere in the module. Collecting every simple assignment first
    lets ``_resolve_methods`` follow names regardless of definition order.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) or isinstance(
            node, (ast.AnnAssign, ast.NamedExpr)
        ):
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
            else:
                targets = [node.target] if isinstance(node.target, ast.Name) else []
            for target in targets:
                aliases[target.id] = node.value
    return aliases


def _resolve_methods(expr, method_aliases, seen=None):
    """Return method strings for a literal/aliased ``methods`` expression.

    Handles a single string, a list/tuple/set of strings, and names bound to any
    of those (including nested aliases). Returns ``None`` when the expression
    cannot be resolved from source -- callers must fail closed in that case.
    """
    if seen is None:
        seen = set()
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        methods = []
        for elt in expr.elts:
            if isinstance(elt, ast.Starred):
                return None
            resolved = _resolve_methods(elt, method_aliases, seen)
            if resolved is None:
                return None
            methods.extend(resolved)
        return methods
    if isinstance(expr, ast.Name):
        name = expr.id
        if name in seen or name not in method_aliases:
            return None
        return _resolve_methods(method_aliases[name], method_aliases, seen | {name})
    return None


def _dynamic_routes_in_tree(tree: ast.AST, relative: str):
    """Yield (key, protected) for each mutating dynamic route registration.

    Covers both ``add_api_route`` and Starlette's low-level ``add_route``, plus
    local aliases of either. ``add_route`` binds the endpoint by reference, so
    the auth gate lives on the handler function rather than on the registration
    call. Resolve that handler within the same module and reuse ``_is_protected``.
    A registration counts as guarded when either its own ``dependencies=[...]``
    carry an auth-flavoured ``Depends`` or the resolved endpoint does.

    The key shape mirrors the decorator sweep: (source file, method, raw path).
    One registration can declare several methods; each mutating method is
    yielded separately. An unresolvable endpoint or ``methods`` expression fails
    closed (the latter under the ``UNRESOLVED_METHOD`` sentinel). A registration
    with no ``methods`` is Starlette's read-only GET default and is not swept,
    unless a ``**kwargs`` bag could hide a mutating verb.
    """
    registrar_aliases = _canonical_names(tree)
    method_aliases = _method_aliases(tree)
    functions_by_name = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions_by_name.setdefault(node.name, []).append(node)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        registrar = None
        if isinstance(func, ast.Attribute):
            if func.attr in DYNAMIC_REGISTRARS:
                registrar = func.attr
        elif isinstance(func, ast.Name):
            # Resolve (possibly aliased) bare-name registrations. A bare call
            # spelled with a registrar name but no alias is counted too (fail
            # closed).
            candidate = registrar_aliases.get(func.id, func.id)
            if candidate in DYNAMIC_REGISTRARS:
                registrar = candidate
        if registrar is None:
            continue

        raw_path = ""
        if node.args and isinstance(node.args[0], ast.Constant):
            raw_path = node.args[0].value

        methods_kw = next((kw for kw in node.keywords if kw.arg == "methods"), None)
        if methods_kw is None:
            # No ``methods`` means Starlette's GET default (read-only), but a
            # ``**kwargs`` bag may smuggle a mutating verb -- fail closed there.
            methods = (
                [UNRESOLVED_METHOD]
                if any(kw.arg is None for kw in node.keywords)
                else []
            )
        else:
            resolved = _resolve_methods(methods_kw.value, method_aliases)
            methods = resolved if resolved is not None else [UNRESOLVED_METHOD]

        endpoint = node.args[1] if len(node.args) > 1 else None
        if endpoint is None:
            for kw in node.keywords:
                if kw.arg == "endpoint":
                    endpoint = kw.value
                    break
        endpoint_name = ""
        if isinstance(endpoint, ast.Name):
            endpoint_name = endpoint.id
        elif isinstance(endpoint, ast.Attribute):
            endpoint_name = endpoint.attr

        protected_by_registration = any(
            _looks_like_auth(name)
            for kw in node.keywords
            if kw.arg == "dependencies"
            for name in _dependency_names(kw.value)
        )
        protected_by_endpoint = any(
            _is_protected(fn) for fn in functions_by_name.get(endpoint_name, [])
        )

        for method in methods:
            lowered = method.lower()
            if lowered in MUTATING or method == UNRESOLVED_METHOD:
                yield (
                    (relative, lowered, raw_path),
                    (protected_by_registration or protected_by_endpoint),
                )


def _add_api_route_registrations():
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
            yield from _dynamic_routes_in_tree(tree, relative)


_DYNAMIC_ROUTES = list(_add_api_route_registrations())


def test_add_api_route_scan_is_not_vacuous():
    assert _DYNAMIC_ROUTES, "no add_api_route registrations discovered"
    mutating = [key for key, _ in _DYNAMIC_ROUTES]
    assert len(mutating) >= 6, (
        f"expected the known dynamic mutating registrations, found: {mutating}"
    )


def test_every_mutating_add_api_route_is_protected_or_justified_public():
    offenders = [
        f"{rel}: {method.upper()} {path}"
        for (rel, method, path), protected in _DYNAMIC_ROUTES
        if not protected and (rel, method, path) not in PUBLIC_DYNAMIC_MUTATING_ROUTES
    ]
    assert not offenders, (
        "Dynamically registered mutating routes (add_api_route / add_route) with "
        "no auth dependency or permission guard on the registration or its "
        "endpoint:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_public_dynamic_allowlist_has_no_stale_entries():
    present = {key for key, protected in _DYNAMIC_ROUTES if not protected}
    stale = sorted(set(PUBLIC_DYNAMIC_MUTATING_ROUTES) - present)
    assert not stale, (
        "These dynamic public-route entries are now guarded or no longer exist; "
        "remove them from PUBLIC_DYNAMIC_MUTATING_ROUTES:\n  "
        + "\n  ".join(f"{rel}: {m.upper()} {p}" for rel, m, p in stale)
    )


def test_add_api_route_detector_flags_known_bad_registration():
    """Non-vacuity guard: the detector must flag an unguarded registration.

    Two synthetic registrations are parsed from source rather than read from
    disk: one endpoint has no auth at all, the other carries the canonical
    ``Depends(get_current_user)``. The detector must reject the former and
    accept the latter, proving the invariant test above is actually checking
    something and is not silently passing on an empty/unparseable sweep.
    """
    synthetic_source = """
class SyntheticMixin:
    def _register(self):
        self.router.add_api_route("/bad", self._bad, methods=["POST"])
        self.router.add_api_route("/good", self._good, methods=["DELETE"])

    async def _bad(self, body: str):
        return body

    async def _good(self, body: str, current_user=Depends(get_current_user)):
        return body
"""
    tree = ast.parse(synthetic_source)
    found = {
        key: protected
        for key, protected in _dynamic_routes_in_tree(
            tree, "src/api/routes/synthetic.py"
        )
    }
    assert found[("src/api/routes/synthetic.py", "post", "/bad")] is False, (
        "detector failed to flag an unguarded mutating add_api_route"
    )
    assert found[("src/api/routes/synthetic.py", "delete", "/good")] is True, (
        "detector failed to recognise Depends(get_current_user) on an endpoint"
    )


def test_aliased_add_api_route_registrations_are_inventoried():
    """Aliased runtime registrations must not slip past the inventory.

    ``add = router.add_api_route`` then ``add(...)`` is an ``ast.Name`` call the
    attribute-only detector used to skip entirely, so every invariant passed
    vacuously for that route. Fixpoint resolution must inventory both the direct
    alias and a chained one, flag the unguarded endpoints, honour a live guard,
    and leave a literal read-only alias unflagged (fail closed, not noisy).
    """
    source = """
router = APIRouter()
add = router.add_api_route
wire = add

async def nuke(request, body=None):
    return None

async def guarded(request, current_user=Depends(get_current_user), body=None):
    return None

add("/aliased-nuke", nuke, methods=["POST"])
wire("/chained-nuke", nuke, methods=["DELETE"])
add("/aliased-good", guarded, methods=["POST"])
add("/aliased-readonly", nuke, methods=["GET"])
wire("/chained-readonly", nuke, methods=["GET"])
"""
    tree = ast.parse(source)
    aliases = _canonical_names(tree)
    assert aliases["add"] == "add_api_route"
    assert aliases["wire"] == "add_api_route"  # chained alias collapses

    found = dict(_dynamic_routes_in_tree(tree, "src/api/routes/synthetic.py"))
    base = "src/api/routes/synthetic.py"
    # Aliased unguarded mutations are inventoried and flagged.
    assert found[(base, "post", "/aliased-nuke")] is False
    assert found[(base, "delete", "/chained-nuke")] is False
    # A live auth dependency still protects an aliased registration.
    assert found[(base, "post", "/aliased-good")] is True
    # Literal read-only aliases are not over-flagged.
    assert not any(
        path in {"/aliased-readonly", "/chained-readonly"}
        for _, _, path in found
    ), f"read-only alias over-flagged: {found}"


def test_guard_name_matcher_rejects_response_model_lookalikes():
    """Non-vacuity: an auth word inside a symbol name is not a guard call.

    Mirrors the decorator-sweep vacuity: ``PasskeyAuthenticateOptionsResponse``
    and ``authenticated_cache_key`` used to be accepted as authentication by an
    unanchored substring search. They must not satisfy this invariant either.
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
        "_authenticate",
        "verify_token",
        "require_admin_permission",
        "_require_channel_access",
        "check_permission",
    ):
        assert _is_guard_call_name(guard), guard


def test_dead_nested_guard_does_not_protect_dynamic_route():
    """A guard inside a never-called nested callable must not gate an endpoint.

    ``ast.walk(fn)`` used to descend into nested functions/lambdas, so a dead
    guard there made an unauthenticated dynamically registered route pass. A
    live guard inside statement-level nesting must still count.
    """
    source = """
class SyntheticMixin:
    def _register(self):
        self.router.add_api_route("/dead-nested", self._dead_nested, methods=["POST"])
        self.router.add_api_route("/dead-lambda", self._dead_lambda, methods=["POST"])
        self.router.add_api_route("/live", self._live, methods=["POST"])

    async def _dead_nested(self, body: str):
        def never_called():
            check_admin_permission(body)
        return body

    async def _dead_lambda(self, body: str):
        _ = lambda: verify_token(body)
        return body

    async def _live(self, body: str):
        if body:
            check_admin_permission(body)
        return body
"""
    found = dict(
        _dynamic_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    assert found[("src/api/routes/synthetic.py", "post", "/dead-nested")] is False
    assert found[("src/api/routes/synthetic.py", "post", "/dead-lambda")] is False
    assert found[("src/api/routes/synthetic.py", "post", "/live")] is True


def test_add_route_detector_flags_unguarded_mutating_route():
    """Non-vacuity for Starlette's low-level ``add_route`` registration form.

    Before this form was inventoried, a mutating endpoint registered through it
    was invisible to every route-auth guard. The synthetic unguarded POST must
    be flagged, a guarded DELETE accepted, and a literal GET left unflagged.
    """
    source = """
class SyntheticMixin:
    def _register(self):
        self.router.add_route("/bad", self._bad, methods=["POST"])
        self.router.add_route("/good", self._good, methods=["DELETE"])
        self.router.add_route("/readonly", self._bad, methods=["GET"])

    async def _bad(self, body: str):
        return body

    async def _good(self, body: str, current_user=Depends(get_current_user)):
        return body
"""
    found = dict(
        _dynamic_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    base = "src/api/routes/synthetic.py"
    assert found[(base, "post", "/bad")] is False, (
        "detector failed to flag an unguarded mutating add_route"
    )
    assert found[(base, "delete", "/good")] is True, (
        "detector failed to recognise Depends(get_current_user) on an add_route endpoint"
    )
    assert not any(path == "/readonly" for _, _, path in found), found


def test_add_route_alias_and_method_alias_resolution():
    """Aliases of both the registrar and the methods collection are resolved.

    ``add = router.add_route`` is an ``ast.Name`` call and ``methods=VERBS``
    points at a module-level literal. Neither may silently drop a mutating
    registration; a guarded endpoint still passes and a read-only alias leaves
    the inventory clean.
    """
    source = """
router = APIRouter()
add = router.add_route
wire = add
VERBS = ["POST"]
READ_ONLY = ["GET"]

async def nuke(request, body=None):
    return None

async def guarded(request, current_user=Depends(get_current_user), body=None):
    return None

add("/aliased-nuke", nuke, methods=VERBS)
wire("/chained-nuke", nuke, methods=["DELETE"])
add("/aliased-good", guarded, methods=VERBS)
add("/readonly", nuke, methods=READ_ONLY)
"""
    tree = ast.parse(source)
    aliases = _canonical_names(tree)
    assert aliases["add"] == "add_route"
    assert aliases["wire"] == "add_route"  # chained alias collapses

    found = dict(_dynamic_routes_in_tree(tree, "src/api/routes/synthetic.py"))
    base = "src/api/routes/synthetic.py"
    assert found[(base, "post", "/aliased-nuke")] is False
    assert found[(base, "delete", "/chained-nuke")] is False
    assert found[(base, "post", "/aliased-good")] is True
    assert not any(path == "/readonly" for _, _, path in found), found


def test_unresolvable_methods_fail_closed_for_add_route():
    """A non-literal ``methods`` cannot silently drop a mutating registration."""
    source = """
router = APIRouter()

async def nuke(request, body=None):
    return None

async def guarded(request, current_user=Depends(get_current_user), body=None):
    return None

router.add_route("/variable", nuke, methods=METHODS)
router.add_route("/starred", nuke, methods=[*METHODS])
router.add_route("/bag", nuke, **opts)
router.add_route("/guarded-variable", guarded, methods=METHODS)
router.add_route("/plain-get", nuke)
"""
    found = dict(
        _dynamic_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    base = "src/api/routes/synthetic.py"
    for path in ("/variable", "/starred", "/bag"):
        assert found[(base, UNRESOLVED_METHOD, path)] is False, path
    assert found[(base, UNRESOLVED_METHOD, "/guarded-variable")] is True
    # No methods means Starlette's read-only GET default -- not swept.
    assert not any(path == "/plain-get" for _, _, path in found), found


def test_add_websocket_route_is_not_swept_as_http_route():
    """WS registration stays out of the HTTP inventory; WS has its own auth.

    ``add_websocket_route`` is a different registrar with its own opcode
    handshake. Sweeping it here would be incorrect and noisy, so neither the
    direct nor the aliased form may appear in the dynamic route inventory.
    """
    source = """
router = APIRouter()

async def ws_endpoint(websocket):
    await websocket.close()

router.add_websocket_route("/ws", ws_endpoint)
wsadd = router.add_websocket_route
wsadd("/ws-alias", ws_endpoint, methods=["POST"])
"""
    found = dict(
        _dynamic_routes_in_tree(ast.parse(source), "src/api/routes/synthetic.py")
    )
    assert not found, f"WS registration was swept as an HTTP route: {found}"
