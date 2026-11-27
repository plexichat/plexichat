"""SEC700 invariant: live authorization gates run before cache reads.

Several read paths cache expensive per-viewer query results or rendered bodies.
Those caches deliberately hold *data only* -- never the authorization decision.
If a gate is moved below a cache read, a viewer whose access was revoked can be
served a still-warm body, so every configured call site must keep the live gate
textually before the cache lookup, inside the same function.

This is a static AST invariant: no app import, config, DB or network is needed,
so it cannot flake under the default test selection. The ordering detector is
validated against synthetic good/bad snippets before the real tree is trusted,
and a non-vacuity test proves every configured call site is actually discovered
(with both a gate and a cache read present) rather than silently skipped.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]

# (source path, function, live-gate call names, cache-read call names)
# Deliberately excludes paths such as reactions.get_reactions_batch, where the
# gate intentionally runs only on a cache HIT (the cold path enforces access
# inline); a naive "gate always first" rule does not apply there.
SITES = [
    # ``get_poll`` gates through ``_can_view_poll_message`` (participation plus a
    # live channels.view grant); the name ``get_message`` is kept for sites that
    # call the messaging layer directly.
    (
        "src/core/polls/manager/reading.py",
        "get_poll",
        {"get_message", "_can_view_poll_message"},
        {"get"},
    ),
    ("src/core/polls/manager/voting.py", "get_results", {"can_access_poll"}, {"get"}),
    (
        "src/core/reactions/manager/reactions.py",
        "get_reaction_users",
        {"_get_message", "_is_participant"},
        {"_cached_reaction_read"},
    ),
    (
        "src/core/reactions/manager/reactions.py",
        "get_user_reactions",
        {"_get_message", "_is_participant"},
        {"_cached_reaction_read"},
    ),
    (
        "src/core/auth/managers/session/verification.py",
        "verify_token",
        {"is_malformed_token_input", "check_rate_limit"},
        {"_verify_token"},
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


def _call_linenos(node, names):
    """Sorted line numbers of calls whose function name is in ``names``.

    Matches both bare calls (``gate(...)``) and attribute calls
    (``self.gate(...)``). Returns *all* matches so the caller can use the
    earliest one -- a single stray call found by ``ast.walk`` order would make
    the ordering check order-dependent and flaky.
    """
    lines = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if isinstance(func, ast.Name) and func.id in names:
            lines.append(inner.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in names:
            lines.append(inner.lineno)
    return sorted(lines)


def _check_order(tree, funcname, gates, cache_names):
    """Return ``(ok, reason)`` for gate-before-cache ordering at one site."""
    fn = _find_function(tree, funcname)
    if fn is None:
        return False, "function not found"
    gate_lines = _call_linenos(fn, gates)
    cache_lines = _call_linenos(fn, cache_names)
    if not gate_lines:
        return False, "no authz gate call in function"
    if not cache_lines:
        return False, "no cache read call in function"
    if min(gate_lines) < min(cache_lines):
        return True, "ok"
    return False, (
        f"first gate at line {min(gate_lines)}, "
        f"first cache read at line {min(cache_lines)}"
    )


def test_self_test_detector_catches_gate_after_cache():
    """The detector flags a gate moved below a cache read (and missing nodes)."""
    good = textwrap.dedent(
        """
        def read(self, uid):
            if not self.gate(uid):
                return None
            return self.cache.get(uid)
        """
    )
    bad = textwrap.dedent(
        """
        def read(self, uid):
            cached = self.cache.get(uid)
            if cached is not None:
                return cached
            if not self.gate(uid):
                return None
        """
    )
    no_gate = textwrap.dedent(
        """
        def read(self, uid):
            return self.cache.get(uid)
        """
    )
    no_cache = textwrap.dedent(
        """
        def read(self, uid):
            if not self.gate(uid):
                return None
            return compute(uid)
        """
    )
    assert _check_order(ast.parse(good), "read", {"gate"}, {"get"})[0] is True

    ok, reason = _check_order(ast.parse(bad), "read", {"gate"}, {"get"})
    assert ok is False and "first gate at line" in reason

    assert _check_order(ast.parse(no_gate), "read", {"gate"}, {"get"})[0] is False
    assert _check_order(ast.parse(no_cache), "read", {"gate"}, {"get"})[0] is False
    assert _check_order(ast.parse(good), "absent", {"gate"}, {"get"})[0] is False


def test_scan_is_not_vacuous():
    """Every configured site must exist and contain both a gate and a read."""
    assert len(SITES) >= 4
    for rel, func, gates, cache_names in SITES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        fn = _find_function(tree, func)
        assert fn is not None, f"{rel}:{func} not found"
        assert _call_linenos(fn, gates), f"{rel}:{func} gate call not found"
        assert _call_linenos(fn, cache_names), f"{rel}:{func} cache read not found"


@pytest.mark.parametrize(
    "rel,func,gates,cache_names", SITES, ids=[f"{s[1]}" for s in SITES]
)
def test_gate_precedes_cache_read(rel, func, gates, cache_names):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    ok, reason = _check_order(tree, func, gates, cache_names)
    assert ok, f"{rel}:{func}: {reason}"
