"""Advisory-lock namespace isolation across every quota guard.

Quota guards that enforce per-resource caps under PostgreSQL READ COMMITTED
take a transaction-scoped advisory lock (``pg_advisory_xact_lock``) keyed by the
guarded resource. Every quota must own a namespace (or a small reserved band)
that no other quota touches. Two quotas sharing a namespace would serialise
against each other even though they are unrelated -- safe for correctness
(over-serialisation, never a quota bypass) but wasteful and a maintenance trap.

Besides quota guards, permission-based check-then-write sites whose gate cannot
be expressed in SQL (role JSON + owner shortcut + overrides) re-run
``require_permission`` under a per-server advisory lock; those permission guards
own their own namespaces too (142 for delete_event, 143 for update_server) and
are registered here so they cannot collide with a quota namespace.

Commit 9313d994 introduced this guard ratchet but only covered the role and
scheduled-event quotas. The invite, emoji, sticker, soundboard, webhook,
application, bookmark and storage locks were left unguarded, so a namespace
collision in any of them -- or a brand new advisory lock added anywhere in
``src/`` -- would pass the guard silently.

This module is a complete, non-vacuous inventory of all production advisory
xact locks. It fails if:
  * a new advisory-lock call site appears that is not registered below,
  * a registered quota's namespace changes or collides with another quota,
  * a lock key degenerates to a bare constant (no resource derivation),
  * a single-key (64-bit) lock silently becomes a namespaced two-key lock.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[2]

# Definitive map of all production two-argument pg_advisory_xact_lock call
# sites. Value: (path relative to src/, set of namespaces that quota owns).
# A quota may own a small band: invite uses 2 for server scope and 1 for
# inviter scope; emoji uses 100/101 for the animated flag.
_QUOTA_LOCKS = {
    "invite": ("core/servers/handlers/members/invites.py", {1, 2}),
    "emoji": ("core/reactions/manager/emojis.py", {100, 101}),
    "sticker": ("core/stickers/manager.py", {102}),
    "soundboard": ("core/soundboard/manager.py", {120}),
    "webhook": ("core/webhooks/manager/crud.py", {130}),
    # 140 = create_event quota; 142 = delete_event permission re-check guard.
    "scheduled-event": ("core/servers/manager/scheduled_event_ops.py", {140, 142}),
    "role": ("core/servers/handlers/roles/crud.py", {141}),
    # update_server re-checks ``server.manage`` under namespace 143.
    "server-settings-permission": ("core/servers/manager/server_ops.py", {143}),
    "application": ("core/applications/manager/application_crud.py", {150}),
    "bookmark": ("core/messaging/services/bookmarks.py", {0x424F4B4D}),
}

# Single-argument locks use the whole 64-bit keyspace (no first-key namespace)
# and so cannot collide on a namespace. Registered so a new one cannot slip
# through unchecked, and so a switch to a two-key form is caught.
_SINGLE_ARG_LOCKS = {
    "storage": ("core/storage_quota/manager.py",),
}

_ALL_REGISTERED_PATHS = {path for path, _ in _QUOTA_LOCKS.values()} | {
    path for (path,) in _SINGLE_ARG_LOCKS.values()
}


def _parse(rel_path):
    return ast.parse((_SRC_ROOT / rel_path).read_text(encoding="utf-8"))


def _advisory_lock_calls(tree):
    """Yield ``(call, params_node)`` for each pg_advisory_xact_lock call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        sql = node.args[0]
        if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
            continue
        if "pg_advisory_xact_lock" not in sql.value:
            continue
        yield node, (node.args[1] if len(node.args) > 1 else None)


def _arg_count(sql_node):
    return sql_node.value.count("?")


def _const_int(tree, name):
    """Resolve a module-level integer constant assignment by name."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, int
                    ):
                        return node.value.value
    return None


def _namespace_from_expr(tree, expr):
    """Return the set of first-key values an expression can produce."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, int):
        return {expr.value}
    # Reserved band: ``BASE + flag`` where flag is 0/1 (emoji animated).
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = expr.left
        if isinstance(left, ast.Constant) and isinstance(left.value, int):
            return {left.value, left.value + 1}
    if isinstance(expr, ast.Name):
        value = _const_int(tree, expr.id)
        return {value} if value is not None else set()
    return set()


def _namespace_from_tuple(tree, params):
    if isinstance(params, (ast.Tuple, ast.List)) and params.elts:
        return _namespace_from_expr(tree, params.elts[0])
    return set()


def _namespace_from_helper(tree, params):
    """Resolve a helper call returning the ``(namespace, key)`` tuple."""
    if not isinstance(params, ast.Call):
        return set()
    func = params.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(
                    ret.value, (ast.Tuple, ast.List)
                ):
                    return _namespace_from_expr(tree, ret.value.elts[0])
    return set()


def _namespaces_for(tree):
    """Union of all first-key namespaces used by a module's xact locks."""
    found = set()
    for call, params in _advisory_lock_calls(tree):
        if _arg_count(call.args[0]) < 2:
            continue
        found |= _namespace_from_tuple(tree, params)
        found |= _namespace_from_helper(tree, params)
    return found


def _prod_lock_paths():
    """All non-test source files containing a real pg_advisory_xact_lock call."""
    paths = set()
    for path in _SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(_SRC_ROOT)
        if "tests" in rel.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "pg_advisory_xact_lock" not in text:
            continue
        if any(True for _ in _advisory_lock_calls(ast.parse(text))):
            paths.add(rel.as_posix())
    return paths


def test_every_advisory_lock_is_registered():
    """Non-vacuous: every production xact lock must be in the registry."""
    discovered = _prod_lock_paths()
    assert discovered, "no advisory locks found - the guard would pass vacuously"
    unregistered = discovered - _ALL_REGISTERED_PATHS
    assert not unregistered, (
        f"unregistered pg_advisory_xact_lock call sites: {sorted(unregistered)}; "
        "add each to _QUOTA_LOCKS/_SINGLE_ARG_LOCKS with its namespace"
    )
    stale = _ALL_REGISTERED_PATHS - discovered
    assert not stale, (
        f"registry references files with no advisory lock: {sorted(stale)}"
    )


def test_all_quota_namespaces_are_distinct_and_stable():
    """Every quota owns a stable namespace band disjoint from all others."""
    actual = {}
    for quota, (rel_path, expected) in _QUOTA_LOCKS.items():
        namespaces = _namespaces_for(_parse(rel_path))
        assert namespaces, f"{quota}: failed to extract a namespace from {rel_path}"
        assert namespaces == expected, (
            f"{quota}: namespace set changed: expected {sorted(expected)}, "
            f"got {sorted(namespaces)}"
        )
        actual[quota] = namespaces

    names = list(actual)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            overlap = actual[left] & actual[right]
            assert not overlap, (
                f"quotas {left!r} and {right!r} share namespace(s) {sorted(overlap)}; "
                "unrelated quotas must serialise on distinct namespaces"
            )


def test_role_and_scheduled_event_still_have_distinct_namespaces():
    """Original 9313d994 regression: role and scheduled-event must not collide."""
    role_ns = _namespaces_for(_parse(_QUOTA_LOCKS["role"][0]))
    event_ns = _namespaces_for(_parse(_QUOTA_LOCKS["scheduled-event"][0]))
    assert role_ns and event_ns
    assert not (role_ns & event_ns), (
        "role and scheduled-event quota locks share a namespace"
    )


def test_single_arg_locks_have_no_namespace_to_collide():
    """Single-argument locks use the whole 64-bit keyspace, not a namespace."""
    for quota, (rel_path,) in _SINGLE_ARG_LOCKS.items():
        calls = list(_advisory_lock_calls(_parse(rel_path)))
        assert calls, f"{quota}: no advisory lock found in {rel_path}"
        assert all(_arg_count(call.args[0]) == 1 for call, _ in calls), (
            f"{quota}: expected a single-argument advisory lock"
        )


def test_lock_keys_derive_from_resource():
    """No quota lock key may be a bare integer constant."""
    for quota, (rel_path, _expected) in _QUOTA_LOCKS.items():
        tree = _parse(rel_path)
        for call, params in _advisory_lock_calls(tree):
            if _arg_count(call.args[0]) < 2:
                continue
            if isinstance(params, (ast.Tuple, ast.List)) and len(params.elts) > 1:
                key = params.elts[1]
                assert not (
                    isinstance(key, ast.Constant) and isinstance(key.value, int)
                ), f"{quota}: advisory-lock key must derive from a resource id"
    for quota, (rel_path,) in _SINGLE_ARG_LOCKS.items():
        for _call, params in _advisory_lock_calls(_parse(rel_path)):
            if isinstance(params, (ast.Tuple, ast.List)) and params.elts:
                key = params.elts[0]
                assert not (
                    isinstance(key, ast.Constant) and isinstance(key.value, int)
                ), f"{quota}: advisory-lock key must derive from a resource id"
