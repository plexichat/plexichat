"""SEC invariant: server library reads gate membership BEFORE the cache lookup.

Sticker and soundboard library pages are memoized per viewer. Their cold paths
check membership inside the cached function, but a warm cache hit skips that
body entirely. Core membership revocations (kick/ban/leave driven by automod,
an admin path or a bot) clear the core membership caches but not these HTTP
response namespaces, so a removed member could otherwise keep reading a
still-warm 200 until the short TTL elapsed. Both list routes now run an
independent, fail-closed membership gate before the cached loader is entered.

This is a static AST invariant (no app import, DB or network needed), matching
``test_sec700_authz_gate_before_cache.py``. The detector is validated against
synthetic good/bad snippets and a non-vacuity check proves every configured
site is actually discovered.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

# (source path, route function, cached loader name, gate helper name)
SITES = [
    (
        "src/api/routes/stickers.py",
        "get_server_stickers",
        "_load_server_stickers_page",
        "_require_server_member_for_library",
    ),
    (
        "src/api/routes/soundboard.py",
        "list_server_sounds",
        "_load_server_sounds_page",
        "_require_server_member_for_library",
    ),
]


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(root: ast.AST):
    """Yield nodes on ``root``'s own execution path, skipping nested callables.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but never into a nested function, async function or lambda. A loader call
    in a helper that is merely defined does not perform a cache read, so it
    must not establish (or distort the ordering of) the cached-loader evidence.
    """
    stack = [root]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _call_lines(node, name):
    lines = []
    for inner in _own_body_nodes(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(inner.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(inner.lineno)
    return sorted(lines)


def _top_level_call_lines(node, name):
    """Line numbers of calls to ``name`` made directly from the function body.

    A gate nested inside ``if``/``try`` is not an unconditional gate: a warm
    cache hit could reach the loader without ever passing it.
    """
    lines = []
    for stmt in node.body:
        # Only a bare call statement counts. A call nested in an ``if``/``try``
        # body is conditional and must not satisfy the unconditional-gate rule.
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        func = stmt.value.func
        if (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        ):
            lines.append(stmt.lineno)
    return sorted(lines)


def _check_order(tree, route_func, gate, loader):
    fn = _find_function(tree, route_func)
    if fn is None:
        return False, "route function not found"
    gate_lines = _top_level_call_lines(fn, gate)
    loader_lines = _call_lines(fn, loader)
    if not gate_lines:
        return False, "no unconditional gate call in route"
    if not loader_lines:
        return False, "no cached loader call in route"
    if min(gate_lines) < min(loader_lines):
        return True, "ok"
    return False, (
        f"first gate at line {min(gate_lines)}, "
        f"first cache read at line {min(loader_lines)}"
    )


def test_detector_catches_gate_after_cache_and_nested_gate():
    good = textwrap.dedent(
        """
        async def read():
            gate()
            return await loader()
        """
    )
    bad = textwrap.dedent(
        """
        async def read():
            value = await loader()
            gate()
            return value
        """
    )
    nested = textwrap.dedent(
        """
        async def read():
            if something:
                gate()
            return await loader()
        """
    )
    tree = ast.parse(good)
    assert _check_order(tree, "read", "gate", "loader")[0] is True
    tree = ast.parse(bad)
    ok, reason = _check_order(tree, "read", "gate", "loader")
    assert ok is False and "first gate" in reason
    tree = ast.parse(nested)
    ok, reason = _check_order(tree, "read", "gate", "loader")
    assert ok is False and "unconditional" in reason


def test_dead_nested_loader_call_is_not_cache_evidence():
    """A loader call in a never-invoked nested callable is not a cache read.

    Regression: ``_call_lines`` used ``ast.walk(node)``, which descends into
    nested functions and lambdas. A dead early loader call could both invent a
    cache read and mask a real gate-after-cache regression by skewing ``min``.
    """
    dead_nested = textwrap.dedent(
        """
        async def read():
            gate()
            def never_called():
                return loader()
            return True
        """
    )
    dead_lambda = textwrap.dedent(
        """
        async def read():
            gate()
            _ = lambda: loader()
            return True
        """
    )
    live_nested = textwrap.dedent(
        """
        async def read():
            gate()
            if True:
                return await loader()
            return None
        """
    )
    for source in (dead_nested, dead_lambda):
        fn = _find_function(ast.parse(source), "read")
        # Confirm the old broad traversal would have credited the dead call.
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "loader"
            for n in ast.walk(fn)
        ), "old logic saw dead evidence"
        assert _call_lines(fn, "loader") == []
        ok, reason = _check_order(ast.parse(source), "read", "gate", "loader")
        assert ok is False and "no cached loader call" in reason, reason

    fn = _find_function(ast.parse(live_nested), "read")
    assert _call_lines(fn, "loader"), "live in-body loader call must still count"
    assert _check_order(ast.parse(live_nested), "read", "gate", "loader")[0] is True

    masked = textwrap.dedent(
        """
        async def read():
            def never_called():
                return loader()
            value = await loader()
            gate()
            return value
        """
    )
    ok, reason = _check_order(ast.parse(masked), "read", "gate", "loader")
    assert ok is False and "first gate" in reason, reason


def test_scan_is_not_vacuous():
    assert len(SITES) == 2
    for rel, route_func, loader, gate in SITES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert _find_function(tree, route_func) is not None, f"{rel}:{route_func}"
        assert _find_function(tree, gate) is not None, f"{rel}:{gate} missing"
        assert _call_lines(_find_function(tree, route_func), loader), f"{rel}:{loader}"


@pytest.mark.parametrize("rel,route_func,loader,gate", SITES, ids=[s[1] for s in SITES])
def test_gate_precedes_cache_read(rel, route_func, loader, gate):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    ok, reason = _check_order(tree, route_func, gate, loader)
    assert ok, f"{rel}:{route_func}: {reason}"


@pytest.mark.parametrize("rel,route_func,loader,gate", SITES, ids=[s[1] for s in SITES])
def test_membership_gate_fails_closed(rel, route_func, loader, gate):
    """The gate must deny on missing module, checker error and non-member."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    helper = _find_function(tree, gate)
    assert helper is not None
    source = (
        ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), helper) or ""
    )
    assert "get_servers" in source
    assert "get_member" in source
    # Three distinct fail-closed status codes: 503 (module missing),
    # 500 (checker raised) and 403 (non-member).
    for status in ("503", "500", "403"):
        assert f'"code": {status}' in source, f"{rel}: missing {status} denial"
    assert "if not member" in source
