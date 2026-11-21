"""SEC700 completeness guard for ``@cached`` handlers in ``src/api/routes/**``.

The route-layer revocation sweep moved a live authorization check ahead of the
``@cached`` wrapper on every handler that serves resource-scoped private data
(server / channel / member / role / emoji / pin / search / poll / reaction /
presence / sticker / soundboard reads). This module locks that work in with a
*definitive* static enumeration: it walks the whole route tree, finds every
function decorated with ``@cached``, and requires that each one is either

  * **gated** -- a named live authorization check runs before the cache read,
    either as a FastAPI ``Depends(...)`` on the handler / its route decorator
    (resolved before the wrapper executes) or as an explicit call in the caller
    immediately before the cached helper is invoked; or
  * **allowlisted as public / self-scoped** -- with a documented justification
    for why no cross-viewer revocation gate is required (the response is the
    caller's own data, a public projection, or a value whose enforcement is
    applied live outside the cache).

No app, config, DB or network is imported, so the test cannot flake under the
default selection. Completeness is asserted both ways: every discovered cached
function must appear in exactly one registry, and every registry entry must
still exist. A newly added ``@cached`` handler therefore fails closed until it
is classified, and removing a gate from a classified handler fails the tree
check. The detector is validated against synthetic gated/ungated snippets and
proven non-vacuous before the real tree is trusted.
"""

import ast
import os
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

# (relative source path, cached function name) -> live gate symbol(s).
# The gate must be a Depends(...) on the handler/route, or a call in the caller
# that appears before the cached helper is invoked. Resource-scoped handlers
# only; self-scoped/public handlers live in PUBLIC_CACHED below.
GATED_CACHED = {
    ("src/api/routes/channels/crud.py", "_load_channel"): ("_require_channel_access",),
    ("src/api/routes/emojis.py", "_load_server_emojis_page"): (
        "_require_server_membership",
    ),
    ("src/api/routes/emojis.py", "_load_emoji_detail"): ("_require_server_membership",),
    ("src/api/routes/features/bookmarks.py", "_load_bookmarks_page"): (
        "_ensure_list_access",
    ),
    ("src/api/routes/features/profiles.py", "_load_profile_metadata"): (
        "_profile_projection_for",
    ),
    ("src/api/routes/messages/messages_list.py", "get_channel_messages"): (
        "_verify_channel_messages_access",
    ),
    ("src/api/routes/messages/messages_list.py", "get_pinned_messages"): (
        "_verify_pinned_channel_access",
    ),
    ("src/api/routes/messages/search.py", "_search_messages_cached"): (
        "is_participant",
    ),
    ("src/api/routes/polls.py", "_load_poll_detail"): ("can_access_poll",),
    ("src/api/routes/polls.py", "_load_poll_results"): ("can_access_poll",),
    ("src/api/routes/presence.py", "_get_visible_presence_cached"): (
        "_can_view_presence",
    ),
    ("src/api/routes/reactions.py", "_load_reactions"): ("can_access_message",),
    ("src/api/routes/servers/channels.py", "get_server_channels"): (
        "_require_channel_list_access",
    ),
    ("src/api/routes/servers/members.py", "get_server_members"): (
        "_require_member_list_access",
    ),
    ("src/api/routes/servers/roles.py", "get_server_roles"): ("_require_roles_manage",),
    ("src/api/routes/servers/server_crud.py", "get_server"): (
        "_require_server_access",
    ),
    ("src/api/routes/soundboard.py", "_load_server_sounds_page"): (
        "_require_server_member_for_library",
    ),
    ("src/api/routes/stickers.py", "_load_server_stickers_page"): (
        "_require_server_member_for_library",
    ),
}

# (relative source path, cached function name) -> why no revocation gate needed.
PUBLIC_CACHED = {
    (
        "src/api/routes/features/profiles.py",
        "_read_profile_visibility_raw",
    ): "Caches a raw visibility setting value; enforcement is applied live by "
    "_profile_projection_for and the key is invalidated on settings writes.",
    (
        "src/api/routes/messages/read_receipts.py",
        "get_all_unread_counts",
    ): "Caller's own unread counts; authenticated, require_user_token, and "
    "key partitioned by credential.",
    (
        "src/api/routes/presence.py",
        "_get_presence_targets",
    ): "Caller's own broadcast-audience id list, derived from the caller's "
    "relationships; no other viewer's private data is cached.",
    (
        "src/api/routes/relationships/listing.py",
        "_load_relationship_list",
    ): "Caller's own relationship page; require_user_token gate in the caller "
    "and credential-partitioned key.",
    (
        "src/api/routes/servers/server_crud.py",
        "get_servers",
    ): "Caller's own joined-server list; key derived from the authenticated "
    "principal, no per-resource revocation decision to re-run.",
    (
        "src/api/routes/settings.py",
        "get_all_settings",
    ): "Caller's own settings; require_user_token gate and credential-partitioned key.",
    (
        "src/api/routes/settings.py",
        "get_setting",
    ): "Caller's own single setting; require_user_token gate and credential-"
    "partitioned key.",
    (
        "src/api/routes/users/channel.py",
        "get_dm_channels",
    ): "Caller's own DM channels; require_user_token gate and credential-"
    "partitioned key.",
    (
        "src/api/routes/users/profile.py",
        "get_current_user_info",
    ): "Caller's own profile document; key partitioned by credential.",
    (
        "src/api/routes/users/profile.py",
        "get_user",
    ): "Public user projection only (_user_to_public_response); no private "
    "or relationship-gated fields are cached.",
    (
        "src/api/routes/users/settings.py",
        "get_messaging_settings",
    ): "Caller's own messaging settings; require_user_token gate and "
    "credential-partitioned key.",
    (
        "src/api/routes/voice.py",
        "get_ice_servers",
    ): "Caller's own ICE/TURN bootstrap; authenticated and credential-"
    "partitioned (bot namespaces cannot reuse a user's body).",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_cached(fn):
    for deco in fn.decorator_list:
        func = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(func, ast.Name) and func.id == "cached":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "cached":
            return True
    return False


def _parse_tree(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _route_trees():
    for directory, dirs, files in os.walk(ROUTES):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(directory) / filename
            tree = _parse_tree(path)
            if tree is not None:
                yield str(path.relative_to(ROOT)), tree


def _discover():
    """All @cached functions in the route tree, keyed by (relpath, name)."""
    found = {}
    for rel, tree in _route_trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_cached(
                node
            ):
                found[(rel, node.name)] = node
    return found


def _callee_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _depends_names(nodes):
    """Names passed to Depends(...) anywhere under ``nodes``."""
    names = []
    for node in nodes:
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "Depends"
                and inner.args
            ):
                arg = inner.args[0]
                if isinstance(arg, ast.Name):
                    names.append(arg.id)
                elif isinstance(arg, ast.Attribute):
                    names.append(arg.attr)
    return names


def _own_or_route_gate(fn, gates):
    """Depends(...) on the handler signature or its route decorator."""
    # Signature only: do not pick up a Depends() from the function body.
    if set(_depends_names([fn.args])) & set(gates):
        return True
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        for kw in deco.keywords:
            if kw.arg == "dependencies":
                if set(_depends_names([kw.value])) & set(gates):
                    return True
    return False


def _enclosing_function(tree, node):
    """Innermost function body that directly contains ``node``.

    ``ast.walk`` yields outer functions before the ones nested inside them,
    so returning the first container credits a cached call to its outermost
    enclosing function. A gate that only exists in a different (possibly
    never-invoked) sibling helper would then wrongly mark the call as gated.
    Return the deepest container so the caller check is scoped to the function
    that actually invokes the cached helper.
    """
    containers = [
        parent
        for parent in ast.walk(tree)
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
        and parent is not node
        and any(child is node for child in ast.walk(parent))
    ]
    if not containers:
        return None
    innermost = containers[0]
    for candidate in containers[1:]:
        if any(candidate is child for child in ast.walk(innermost)):
            innermost = candidate
    return innermost


def _own_body_gate_calls(caller, gates):
    """Gate calls in ``caller``'s own body, excluding nested callables.

    Merely *defining* a nested function that calls the gate does not run it, so
    a gate call inside a nested function / lambda must not silence the guard.
    """
    hits = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call) and _callee_name(child) in gates:
                hits.append(child)
            walk(child)

    for stmt in caller.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(stmt, ast.Call) and _callee_name(stmt) in gates:
            hits.append(stmt)
        walk(stmt)
    return hits


def _caller_gate_evidence(trees, cached_name, gates):
    """Check every call site of ``cached_name`` has a gate call before it.

    Returns ``(sites, ungated)``. ``sites`` is the number of call sites found;
    ``ungated`` is the list of sites with no declared gate ahead of the call.
    Fails closed: an unresolvable caller or zero call sites is reported as
    ungated. Only gates in the calling function's own body count -- a gate in
    a nested (and possibly never-called) helper is not a live pre-cache check.
    """
    sites = 0
    ungated = []
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee_name(node) != cached_name:
                continue
            sites += 1
            caller = _enclosing_function(tree, node)
            if caller is None:
                ungated.append((_rel, node.lineno, "no enclosing function"))
                continue
            gated = any(
                inner.lineno < node.lineno
                for inner in _own_body_gate_calls(caller, gates)
            )
            if not gated:
                ungated.append((_rel, node.lineno, caller.name))
    return sites, ungated


# ---------------------------------------------------------------------------
# Detector self-tests (synthetic sources, validated before trusting the tree)
# ---------------------------------------------------------------------------

_GOOD_OWN = textwrap.dedent(
    """
    @router.get("/x")
    @cached(ttl=5)
    def read(server_id: str, current_user=Depends(get_current_user),
             _gate: None = Depends(_require_access)):
        return db.read(server_id, current_user)
    """
)

_GOOD_ROUTE = textwrap.dedent(
    """
    @router.get("/x", dependencies=[Depends(_require_access)])
    @cached(ttl=5)
    def read(server_id: str, current_user=Depends(get_current_user)):
        return db.read(server_id, current_user)
    """
)

_BAD = textwrap.dedent(
    """
    @router.get("/x")
    @cached(ttl=5)
    def read(server_id: str, current_user=Depends(get_current_user)):
        return db.read(server_id, current_user)
    """
)

_GOOD_CALLER = textwrap.dedent(
    """
    async def caller(server_id, current_user):
        _require_access(server_id, current_user)
        return await _load_cached(server_id=server_id, current_user=current_user)
    """
)

_BAD_CALLER = textwrap.dedent(
    """
    async def caller(server_id, current_user):
        return await _load_cached(server_id=server_id, current_user=current_user)
    """
)

# A gate that only appears inside a nested callable that is never invoked is
# not a live pre-cache check. Both shapes used to be credited by the detector:
# the first because the outermost function was selected as the caller, the
# second because the whole caller subtree (including nested bodies) was scanned.
_DEAD_GATE_SIBLING = textwrap.dedent(
    """
    async def outer(current_user):
        def never_called():
            _require_access(current_user)
        async def cached_fn():
            return await _load_cached(current_user)
        return await cached_fn()
    """
)

_DEAD_GATE_NESTED = textwrap.dedent(
    """
    async def caller(current_user):
        def never_called():
            _require_access(current_user)
        return await _load_cached(current_user)
    """
)


def _first_fn(source):
    tree = ast.parse(source)
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_detector_recognises_gate_positions():
    gates = {"_require_access"}
    assert _own_or_route_gate(_first_fn(_GOOD_OWN), gates) is True
    assert _own_or_route_gate(_first_fn(_GOOD_ROUTE), gates) is True
    assert _own_or_route_gate(_first_fn(_BAD), gates) is False


def test_detector_flags_ungated_cached_call_site():
    good_tree = ast.parse(_GOOD_CALLER)
    bad_tree = ast.parse(_BAD_CALLER)
    sites, ungated = _caller_gate_evidence(
        [("synthetic.py", good_tree)], "_load_cached", {"_require_access"}
    )
    assert sites == 1 and ungated == []
    sites, ungated = _caller_gate_evidence(
        [("synthetic.py", bad_tree)], "_load_cached", {"_require_access"}
    )
    assert sites == 1 and len(ungated) == 1


def test_detector_rejects_dead_gate_in_sibling_nested_function():
    """A gate in a never-called sibling helper must not gate the cached call.

    Regression: ``ast.walk`` yields the outermost function first, so the old
    ``_enclosing_function`` reported ``outer`` and credited its whole body --
    including the dead helper's gate call -- to the cached call in ``cached_fn``.
    """
    tree = ast.parse(_DEAD_GATE_SIBLING)
    call = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _callee_name(n) == "_load_cached"
    )
    assert _enclosing_function(tree, call).name == "cached_fn"
    sites, ungated = _caller_gate_evidence(
        [("synthetic.py", tree)], "_load_cached", {"_require_access"}
    )
    assert sites == 1 and len(ungated) == 1


def test_detector_rejects_dead_gate_in_nested_helper_of_caller():
    """A gate inside a nested helper of the caller is not a live pre-cache check."""
    tree = ast.parse(_DEAD_GATE_NESTED)
    sites, ungated = _caller_gate_evidence(
        [("synthetic.py", tree)], "_load_cached", {"_require_access"}
    )
    assert sites == 1 and len(ungated) == 1


# ---------------------------------------------------------------------------
# Real-tree invariant
# ---------------------------------------------------------------------------

_DISCOVERED = _discover()
_TREES = list(_route_trees())


def test_scan_is_not_vacuous():
    # The sweep must see a real, sizeable population of cached handlers.
    assert len(_DISCOVERED) >= 25, sorted(_DISCOVERED)
    assert len(GATED_CACHED) >= 15
    assert len(PUBLIC_CACHED) >= 10


def test_registry_is_complete_and_disjoint():
    """Every cached handler is classified exactly once; no stale entries."""
    discovered = set(_DISCOVERED)
    gated = set(GATED_CACHED)
    public = set(PUBLIC_CACHED)
    assert not (gated & public), f"double-classified: {sorted(gated & public)}"
    missing = discovered - (gated | public)
    stale = (gated | public) - discovered
    assert not missing, (
        "Unclassified @cached handlers in src/api/routes/** (gate them or add "
        "a documented PUBLIC_CACHED justification):\n  "
        + "\n  ".join(f"{rel}:{name}" for rel, name in sorted(missing))
    )
    assert not stale, (
        "Registry entries no longer present in the route tree:\n  "
        + "\n  ".join(f"{rel}:{name}" for rel, name in sorted(stale))
    )


@pytest.mark.parametrize(
    "key,gates",
    list(GATED_CACHED.items()),
    ids=[f"{rel}:{name}" for rel, name in GATED_CACHED],
)
def test_gated_cached_handler_has_live_pre_cache_check(key, gates):
    rel, name = key
    fn = _DISCOVERED[key]
    if _own_or_route_gate(fn, gates):
        return
    sites, ungated = _caller_gate_evidence(_TREES, name, set(gates))
    assert sites > 0, f"{rel}:{name}: no call site found for cached helper"
    assert not ungated, (
        f"{rel}:{name}: cached helper invoked without a live pre-cache gate "
        f"({', '.join(gates)}) at: "
        + ", ".join(f"{f}:{line} in {caller}" for f, line, caller in ungated)
    )


def test_public_allowlist_entries_are_documented():
    for key, justification in PUBLIC_CACHED.items():
        assert isinstance(justification, str) and len(justification.strip()) >= 20, (
            f"{key}: PUBLIC_CACHED entries require a concrete justification"
        )
