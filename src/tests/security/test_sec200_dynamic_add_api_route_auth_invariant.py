"""SEC200 invariant: mutating ``add_api_route`` registrations are auth-gated.

The decorator-based sweep lives in
``test_sec200_mutating_route_auth_invariant.py``. That sweep does not see routes
registered at runtime with ``router.add_api_route(...)``: those bind the
endpoint by reference, so no ``@router.post``/``@router.delete`` decorator is
present for the AST to find. This module closes that gap with an independent,
whole-tree AST scan of ``src/api/routes/**``. It imports no app, config or DB
and cannot flake.

For each ``add_api_route`` call we resolve the endpoint function by name within
the same module and reuse the same auth heuristics as the decorator sweep (auth
flavoured ``Depends(...)`` on the endpoint or on the registration's
``dependencies=[...]``, or an in-handler permission/signature guard). Every
mutating registration must be guarded unless explicitly allowlisted as a
legitimately public credential-exchange route.

The detector is proven non-vacuous by a self-test that feeds it a synthetic
unguarded registration and asserts it is flagged.
"""

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

MUTATING = frozenset({"post", "put", "patch", "delete"})

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
    """Resolve local aliases of ``add_api_route`` to its canonical name.

    ``add = router.add_api_route`` followed by ``add(...)`` is a plain
    ``ast.Name`` call, not the ``ast.Attribute`` this detector keys on. Without
    resolution such a state-changing registration is never added to
    ``_DYNAMIC_ROUTES`` and every invariant below passes vacuously for it, even
    when its endpoint carries no auth -- the same name-resolution fail-open
    fixed for the fan-out, cached-route and admin-destructive guards.

    Resolution runs to a fixpoint so chained aliases
    (``add = router.add_api_route; wire = add``) collapse too. A bare call
    spelled ``add_api_route`` with no resolvable alias is still treated as a
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

    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            pairs = []
            if isinstance(node, ast.ImportFrom):
                pairs = [
                    (a.asname or a.name, a.name)
                    for a in node.names
                    if a.name == "add_api_route"
                ]
            elif isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
                canonical = None
                if isinstance(value, ast.Attribute) and value.attr == "add_api_route":
                    canonical = "add_api_route"
                elif isinstance(value, ast.Name):
                    canonical = resolve(value.id)
                if canonical == "add_api_route":
                    pairs = [
                        (t.id, canonical) for t in targets if isinstance(t, ast.Name)
                    ]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                value = node.value
                canonical = None
                if isinstance(value, ast.Attribute) and value.attr == "add_api_route":
                    canonical = "add_api_route"
                elif isinstance(value, ast.Name):
                    canonical = resolve(value.id)
                if canonical == "add_api_route" and isinstance(node.target, ast.Name):
                    pairs = [(node.target.id, canonical)]
            for local, canonical in pairs:
                if aliases.get(local) != canonical:
                    aliases[local] = canonical
                    changed = True
        if not changed:
            break
    return aliases


def _dynamic_routes_in_tree(tree: ast.AST, relative: str):
    """Yield (key, protected) for each mutating ``add_api_route`` registration.

    ``add_api_route`` binds the endpoint by reference, so the auth gate usually
    lives on the handler function rather than on the registration call. Resolve
    that handler within the same module and reuse ``_is_protected``. A
    registration counts as guarded when either its own ``dependencies=[...]``
    carry an auth-flavoured ``Depends`` or the resolved endpoint does.

    The key shape mirrors the decorator sweep: (source file, method, raw path).
    One registration can declare several methods; each mutating method is
    yielded separately. An unresolvable endpoint fails closed (protected=False).
    """
    aliases = _canonical_names(tree)
    functions_by_name = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions_by_name.setdefault(node.name, []).append(node)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr != "add_api_route":
                continue
        elif isinstance(func, ast.Name):
            # Resolve (possibly aliased) bare-name registrations. A bare
            # ``add_api_route(...)`` with no alias is counted too (fail closed).
            if aliases.get(func.id, func.id) != "add_api_route":
                continue
        else:
            continue

        raw_path = ""
        if node.args and isinstance(node.args[0], ast.Constant):
            raw_path = node.args[0].value

        methods = []
        for kw in node.keywords:
            if kw.arg == "methods" and isinstance(
                kw.value, (ast.List, ast.Tuple, ast.Set)
            ):
                methods = [
                    elt.value
                    for elt in kw.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]

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
            if lowered in MUTATING:
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
        "Dynamically registered mutating routes (add_api_route) with no auth "
        "dependency or permission guard on the registration or its endpoint:\n  "
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
