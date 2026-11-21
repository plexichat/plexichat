"""Advisory-lock namespace isolation across every guard.

Quota guards that enforce per-resource caps under PostgreSQL READ COMMITTED
take a transaction-scoped advisory lock (``pg_advisory_xact_lock``) keyed by the
guarded resource. Every quota owns a namespace (or a small reserved band)
that no other quota touches. Two quotas sharing a namespace would serialise
against each other even though they are unrelated -- safe for correctness
(over-serialisation, never a quota bypass) but wasteful and a maintenance trap.

Permission-based check-then-write sites whose gate cannot be expressed in SQL
(role JSON + owner shortcut + overrides) re-run ``require_permission`` under a
per-server advisory lock. Namespaces 142 (delete_event) and 143
(update_server) are deliberately SHARED with every RBAC mutation that can
invalidate those gates (update_role, delete_role, assign_role, remove_role,
assign_roles_from_whitelist), so a re-check + write cannot race a role change.
Over-serialisation is acceptable; an accidental collision between an
exclusive quota band and a shared permission guard is not. Shared locks are
always acquired in ascending order (142 before 143) to avoid deadlock.

Commit 9313d994 introduced this guard ratchet but only covered the role and
scheduled-event quotas. The invite, emoji, sticker, soundboard, webhook,
application, bookmark and storage locks were left unguarded, so a namespace
collision in any of them -- or a brand new advisory lock added anywhere in
``src/`` -- would pass the guard silently.

This module is a complete, non-vacuous inventory of all production advisory
xact locks. It fails if:
  * a new advisory-lock call site appears that is not registered below,
  * a registered quota's exclusive namespace changes or collides with another
    quota or with a shared permission-guard namespace,
  * the exact path inventory of a shared permission namespace changes,
  * a site taking both shared permission locks acquires them out of order,
  * a lock key degenerates to a bare constant (no resource derivation),
  * a single-key (64-bit) lock silently becomes a namespaced two-key lock.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[2]

# Exclusive quota namespaces. Value: (path relative to src/, namespaces owned).
# A quota may own a small band: invite uses 2 for server scope and 1 for
# inviter scope; emoji uses 100/101 for the animated flag. Shared permission
# namespaces are subtracted before comparison (see the shared registry below).
_QUOTA_LOCKS = {
    "invite": ("core/servers/handlers/members/invites.py", {1, 2}),
    "emoji": ("core/reactions/manager/emojis.py", {100, 101}),
    "sticker": ("core/stickers/manager.py", {102}),
    "soundboard": ("core/soundboard/manager.py", {120}),
    "webhook": ("core/webhooks/manager/crud.py", {130}),
    "scheduled-event": ("core/servers/manager/scheduled_event_ops.py", {140}),
    "role": ("core/servers/handlers/roles/crud.py", {141}),
    "application": ("core/applications/manager/application_crud.py", {150}),
    "bookmark": ("core/messaging/services/bookmarks.py", {0x424F4B4D}),
}

# Single-argument locks use the whole 64-bit keyspace (no first-key namespace)
# and so cannot collide on a namespace. Registered so a new one cannot slip
# through unchecked, and so a switch to a two-key form is caught.
_SINGLE_ARG_LOCKS = {
    "storage": ("core/storage_quota/manager.py",),
}

# Namespaces intentionally shared between a gated writer and the RBAC mutations
# that can invalidate its gate. Not "owned" by any single path.
_SHARED_PERMISSION_NAMESPACES = {142, 143}

# Exact path inventory for each shared namespace. Any new (or removed) lock
# site using 142/143 must update this map; the test below compares it against
# the discovered production call sites.
_SHARED_LOCK_PATHS = {
    142: {
        "core/servers/manager/scheduled_event_ops.py",  # delete_event re-check
        "core/servers/handlers/roles/crud.py",  # update_role / delete_role
        "core/servers/handlers/roles/assignments.py",  # role grants / revokes
    },
    143: {
        "core/servers/manager/server_ops.py",  # update_server re-check
        "core/servers/handlers/roles/crud.py",  # update_role / delete_role
        "core/servers/handlers/roles/assignments.py",  # role grants / revokes
    },
}

_ALL_REGISTERED_PATHS = (
    {path for path, _ in _QUOTA_LOCKS.values()}
    | {path for (path,) in _SINGLE_ARG_LOCKS.values()}
    | {path for paths in _SHARED_LOCK_PATHS.values() for path in paths}
)


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
        "add each to _QUOTA_LOCKS/_SINGLE_ARG_LOCKS/_SHARED_LOCK_PATHS"
    )
    stale = _ALL_REGISTERED_PATHS - discovered
    assert not stale, (
        f"registry references files with no advisory lock: {sorted(stale)}"
    )
    assert _SHARED_PERMISSION_NAMESPACES, "shared permission registry is empty"
    for paths in _SHARED_LOCK_PATHS.values():
        assert paths, "a shared permission namespace has an empty path inventory"
        assert paths <= discovered


def test_all_quota_namespaces_are_distinct_and_stable():
    """Every quota owns a stable exclusive band disjoint from all others."""
    actual = {}
    for quota, (rel_path, expected) in _QUOTA_LOCKS.items():
        namespaces = _namespaces_for(_parse(rel_path)) - _SHARED_PERMISSION_NAMESPACES
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
    quota_union = set().union(*actual.values())
    assert not (quota_union & _SHARED_PERMISSION_NAMESPACES), (
        "an exclusive quota namespace collides with a shared permission guard"
    )


def test_role_and_scheduled_event_still_have_distinct_namespaces():
    """Original 9313d994 regression: role and scheduled-event must not collide."""
    role_ns = _namespaces_for(_parse(_QUOTA_LOCKS["role"][0]))
    event_ns = _namespaces_for(_parse(_QUOTA_LOCKS["scheduled-event"][0]))
    role_exclusive = role_ns - _SHARED_PERMISSION_NAMESPACES
    event_exclusive = event_ns - _SHARED_PERMISSION_NAMESPACES
    assert role_exclusive and event_exclusive
    assert not (role_exclusive & event_exclusive), (
        "role and scheduled-event quota locks share a namespace"
    )


def test_shared_permission_namespaces_have_exact_path_inventory():
    """Non-vacuous: shared guard namespaces have exactly the registered users."""
    discovered = _prod_lock_paths()
    for namespace in _SHARED_PERMISSION_NAMESPACES:
        expected = _SHARED_LOCK_PATHS.get(namespace)
        assert expected, f"shared permission namespace {namespace} is not registered"
        actual = {
            rel for rel in discovered if namespace in _namespaces_for(_parse(rel))
        }
        assert actual == expected, (
            f"namespace {namespace}: registered {sorted(expected)}, "
            f"actual {sorted(actual)}"
        )


def test_multi_namespace_sites_acquire_locks_in_fixed_order():
    """Deadlock avoidance: a site taking both shared locks takes 142 first."""
    checked = 0
    for paths in _SHARED_LOCK_PATHS.values():
        for rel in paths:
            tree = _parse(rel)
            for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                pairs = []
                for call, params in _advisory_lock_calls(fn):
                    if _arg_count(call.args[0]) < 2:
                        continue
                    pairs.append((call.lineno, _namespace_from_tuple(tree, params)))
                pairs.sort()
                shared = [
                    ns
                    for _, ns_set in pairs
                    for ns in sorted(ns_set & _SHARED_PERMISSION_NAMESPACES)
                ]
                if len(set(shared)) < 2:
                    continue
                checked += 1
                assert shared.index(142) < shared.index(143), (
                    f"{rel}:{fn.name} acquires shared locks out of order {shared}"
                )
    assert checked >= 3, "no site takes both shared locks - order guard vacuous"


def test_single_arg_locks_have_no_namespace_to_collide():
    """Single-argument locks use the whole 64-bit keyspace, not a namespace."""
    for quota, (rel_path,) in _SINGLE_ARG_LOCKS.items():
        calls = list(_advisory_lock_calls(_parse(rel_path)))
        assert calls, f"{quota}: no advisory lock found in {rel_path}"
        assert all(_arg_count(call.args[0]) == 1 for call, _ in calls), (
            f"{quota}: expected a single-argument advisory lock"
        )


def test_lock_keys_derive_from_resource():
    """No advisory lock key may be a bare integer constant."""
    for rel_path in sorted(_ALL_REGISTERED_PATHS):
        tree = _parse(rel_path)
        for call, params in _advisory_lock_calls(tree):
            two_key = _arg_count(call.args[0]) >= 2
            if isinstance(params, (ast.Tuple, ast.List)) and params.elts:
                key = params.elts[-1]
                assert not (
                    isinstance(key, ast.Constant) and isinstance(key.value, int)
                ), f"{rel_path}: advisory-lock key must derive from a resource id"
            assert two_key or _arg_count(call.args[0]) == 1
