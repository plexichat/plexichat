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

The order and guarded-write checks carry non-vacuity self-tests that feed a
synthetic reversed-order site and a synthetic bare ``WHERE id = ?`` write
through the very same checker used on production code, so a checker that
silently passes everything cannot keep the suite green.
"""

import ast
import re
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
    # Sticker manager owns three exclusive bands: 102 serialises per-pack
    # sticker adds, 121 serialises per-server pack creates and 122 serialises
    # per-user (personal) pack creates. N890 added 121/122 but left them out
    # of this registry; because they are assigned to the function-local
    # ``lock_ns`` variable rather than an inline constant, the old extractor
    # could not see them and a collision with soundboard (120), reaction (103)
    # or any other quota would pass the guard silently. All three are now
    # registered so the distinctness/stability check below fails closed.
    "sticker": ("core/stickers/manager.py", {102, 121, 122}),
    # Reaction add-quota lock. Uses 103, not 102: 102 belongs to stickers and
    # sharing it would over-serialise two unrelated quotas (see the
    # non-vacuity test below, which rejects a synthetic reaction=102 share).
    "reaction": ("core/reactions/manager/reactions.py", {103}),
    "soundboard": ("core/soundboard/manager.py", {120}),
    "webhook": ("core/webhooks/manager/crud.py", {130}),
    "scheduled-event": ("core/servers/manager/scheduled_event_ops.py", {140}),
    "role": ("core/servers/handlers/roles/crud.py", {141}),
    "application": ("core/applications/manager/application_crud.py", {150}),
    # Per-user chunked-upload active-session cap. Namespace 160 is exclusive to
    # this quota and disjoint from every other quota and shared guard.
    "chunked-session": ("core/media/chunked.py", {160}),
    "bookmark": ("core/messaging/services/bookmarks.py", {0x424F4B4D}),
}

# Single-argument locks use the whole 64-bit keyspace (no first-key namespace)
# and so cannot collide on a namespace. Registered so a new one cannot slip
# through unchecked, and so a switch to a two-key form is caught.
_SINGLE_ARG_LOCKS = {
    "storage": ("core/storage_quota/manager.py",),
}

# Two-key locks whose first key is itself a resource hash, not a fixed
# namespace (the OAuth per-IP pending-state cap uses a SHA-256-derived int4
# pair). They occupy the whole keyspace, so no namespace can collide with them.
# Registered so such a lock cannot slip through unchecked and so a regression
# to a fixed constant namespace is caught.
_HASH_KEYED_LOCKS = {
    "oauth-state": ("core/auth/oauth/state.py",),
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
    | {path for (path,) in _HASH_KEYED_LOCKS.values()}
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


def _const_ints(tree, name):
    """All integer constant assignments to ``name`` in a module.

    Unlike the old module-body-only lookup, this walks the whole tree so a
    lock namespace assigned to a *function-local* variable (as the sticker
    pack quotas do with ``lock_ns = 121`` / ``lock_ns = 122``) is resolved
    too. Otherwise those namespaces are invisible to the guard and a
    collision with another quota would pass silently.
    """
    values = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
            values.add(node.value.value)
    return values


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
        return _const_ints(tree, expr.id)
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


def _first_quota_namespace_collision(actual):
    """Return ``(left, right, overlap)`` for the first colliding quota pair."""
    names = list(actual)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            overlap = actual[left] & actual[right]
            if overlap:
                return left, right, overlap
    return None


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

    collision = _first_quota_namespace_collision(actual)
    assert collision is None, (
        f"quotas {collision[0]!r} and {collision[1]!r} share namespace(s) "
        f"{sorted(collision[2])}; "
        "unrelated quotas must serialise on distinct namespaces"
    )
    quota_union = set().union(*actual.values())
    assert not (quota_union & _SHARED_PERMISSION_NAMESPACES), (
        "an exclusive quota namespace collides with a shared permission guard"
    )


def test_quota_collision_guard_flags_synthetic_102_share():
    """Non-vacuity: the collision checker rejects a synthetic reaction=102 share.

    Reactions once reused the sticker namespace 102. Feeding that same overlap
    through the checker used by the production test must be flagged, while the
    real 103/102 split must pass -- so a checker that silently passes every
    namespace cannot keep this suite green.
    """
    collided = _first_quota_namespace_collision({"sticker": {102}, "reaction": {102}})
    assert collided is not None, "collision checker passed a synthetic 102 share"
    assert set(collided[2]) == {102} and {collided[0], collided[1]} == {
        "sticker",
        "reaction",
    }
    distinct = _first_quota_namespace_collision(
        {"sticker": {102}, "reaction": {103}}
    )
    assert distinct is None, "collision checker flagged distinct namespaces"


def test_pack_namespace_collision_guard_flags_synthetic_120_share():
    """Non-vacuity: a pack lock colliding with soundboard's 120 is flagged.

    N890 introduced the pack namespaces (121 server / 122 user) as values of a
    function-local ``lock_ns`` variable. The previous extractor only resolved
    module-level constants, so these names were invisible to the guard. Feed a
    synthetic ``lock_ns = 120`` through the very same extractor used by the
    production stability test: it must resolve to {120} and the collision
    checker must reject it, while the real 121/122 values stay distinct from
    the soundboard (120) band. A checker that cannot see local assignments
    would return an empty set here and keep the suite green vacuously.
    """
    collided_src = """
def create_pack(db, server_id):
    lock_ns = 120
    db.execute(
        "SELECT pg_advisory_xact_lock(?, ?)",
        (lock_ns, int(server_id) & 0x3FFFFFFF),
    )
"""
    collided_ns = _namespaces_for(ast.parse(collided_src))
    assert collided_ns == {120}, (
        f"synthetic local lock_ns=120 not extracted: {sorted(collided_ns)}; "
        "the pack-namespace guard would be vacuous"
    )
    collision = _first_quota_namespace_collision(
        {"soundboard": {120}, "sticker-pack": collided_ns}
    )
    assert collision is not None, "collision checker passed a synthetic 120 share"
    assert set(collision[2]) == {120} and {collision[0], collision[1]} == {
        "soundboard",
        "sticker-pack",
    }

    # The real pack namespaces must stay distinct from 120 and from each other.
    real = {
        "soundboard": {120},
        "sticker-server-pack": {121},
        "sticker-user-pack": {122},
    }
    assert _first_quota_namespace_collision(real) is None, (
        "collision checker flagged the real, distinct pack namespaces"
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


def _shared_lock_sequence(tree, fn):
    """Shared namespaces acquired by ``fn``, in source (line) order."""
    pairs = []
    for call, params in _advisory_lock_calls(fn):
        if _arg_count(call.args[0]) < 2:
            continue
        pairs.append((call.lineno, _namespace_from_tuple(tree, params)))
    pairs.sort()
    return [
        ns
        for _, ns_set in pairs
        for ns in sorted(ns_set & _SHARED_PERMISSION_NAMESPACES)
    ]


def _is_ascending(shared):
    """True when distinct namespaces are first acquired in ascending order."""
    first_seen = []
    for ns in shared:
        if ns not in first_seen:
            first_seen.append(ns)
    return first_seen == sorted(first_seen)


def _shared_order_violations(tree, functions):
    """Return ``(violations, checked)`` for multi-shared-namespace functions."""
    violations = []
    checked = 0
    for fn in functions:
        shared = _shared_lock_sequence(tree, fn)
        if len(set(shared)) < 2:
            continue
        checked += 1
        if not _is_ascending(shared):
            violations.append(f"{getattr(fn, 'name', '?')}: {shared}")
    return violations, checked


def test_multi_namespace_sites_acquire_locks_in_fixed_order():
    """Deadlock avoidance: a site taking both shared locks takes 142 first."""
    checked = 0
    for paths in _SHARED_LOCK_PATHS.values():
        for rel in paths:
            tree = _parse(rel)
            functions = (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
            violations, count = _shared_order_violations(tree, functions)
            checked += count
            assert not violations, f"{rel}: shared locks out of order: {violations}"
    assert checked >= 3, "no site takes both shared locks - order guard vacuous"


def test_lock_order_guard_flags_synthetic_reversed_site():
    """Non-vacuity: a synthetic 143-then-142 site is flagged, 142-then-143 is not."""
    reversed_src = """
def reversed_order(db):
    db.execute("SELECT pg_advisory_xact_lock(?, ?)", (143, 1))
    db.execute("SELECT pg_advisory_xact_lock(?, ?)", (142, 1))
"""
    ordered_src = """
def ordered_ok(db):
    db.execute("SELECT pg_advisory_xact_lock(?, ?)", (142, 1))
    db.execute("SELECT pg_advisory_xact_lock(?, ?)", (143, 1))
"""
    for src, expect_flagged in ((reversed_src, True), (ordered_src, False)):
        tree = ast.parse(src)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        violations, checked = _shared_order_violations(tree, [fn])
        assert checked == 1, f"synthetic site not recognized as multi-namespace: {src}"
        assert bool(violations) is expect_flagged, (
            f"order checker disagreed on synthetic site (expect flagged="
            f"{expect_flagged}): {violations}"
        )


def test_single_arg_locks_have_no_namespace_to_collide():
    """Single-argument locks use the whole 64-bit keyspace, not a namespace."""
    for quota, (rel_path,) in _SINGLE_ARG_LOCKS.items():
        calls = list(_advisory_lock_calls(_parse(rel_path)))
        assert calls, f"{quota}: no advisory lock found in {rel_path}"
        assert all(_arg_count(call.args[0]) == 1 for call, _ in calls), (
            f"{quota}: expected a single-argument advisory lock"
        )


def test_hash_keyed_two_key_locks_have_no_constant_namespace():
    """Full-keyspace two-key locks must hash the resource, never fix a namespace."""
    assert _HASH_KEYED_LOCKS, "hash-keyed registry is empty - guard is vacuous"
    for quota, (rel_path,) in _HASH_KEYED_LOCKS.items():
        tree = _parse(rel_path)
        calls = list(_advisory_lock_calls(tree))
        assert calls, f"{quota}: no advisory lock found in {rel_path}"
        assert any(_arg_count(call.args[0]) >= 2 for call, _ in calls), (
            f"{quota}: expected a two-key advisory lock"
        )
        assert not _namespaces_for(tree), (
            f"{quota}: expected no fixed namespace on a hash-keyed lock"
        )
        for _call, params in calls:
            assert not isinstance(params, (ast.Tuple, ast.List)), (
                f"{quota}: hash keys must come from a resource-derived helper, "
                "not an inline tuple/constant"
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


# ---------------------------------------------------------------------------
# TOCTOU guarded-write structural guard
# ---------------------------------------------------------------------------
# The behavioural race tests (test_server_delete_owner_toctou.py,
# test_transfer_ownership_guarded_write_toctou.py,
# test_template_creator_guarded_write_toctou.py) inject in-memory mutations to
# prove the fixes work at runtime. Those mutations cannot catch a source
# regression that slips past review, so this registry captures the structural
# shape of each fix. Register every new check-then-write site here (and add its
# behavioural race test); the tests below then fail if the authoritative
# ownership/creator predicate or the rowcount fail-closed guard is dropped, or
# if the write degenerates to a bare ``WHERE id = ?``.
#
# Each entry: site name -> (path relative to src/, function name, guard
# substrings that must appear in one of the function's SQL string literals).
_GUARDED_WRITE_SITES = {
    "delete_server": (
        "core/servers/manager/server_ops.py",
        "delete_server",
        ("AND owner_id = ?",),
    ),
    "transfer_ownership": (
        "core/servers/manager/server_ops.py",
        "transfer_ownership",
        ("AND owner_id = ?", "EXISTS"),
    ),
    "delete_template": (
        "core/servers/manager/template_ops.py",
        "delete_template",
        ("AND creator_id = ?", "EXISTS"),
    ),
    "update_template": (
        "core/servers/manager/template_ops.py",
        "update_template",
        ("AND creator_id = ?",),
    ),
}

_MUTATING_SQL_RE = re.compile(r"^\s*(UPDATE|DELETE)\b", re.IGNORECASE)
_BARE_ID_WRITE_RE = re.compile(
    r"^\s*(UPDATE|DELETE)\b.*\bWHERE\s+id\s*=\s*\?\s*$", re.IGNORECASE | re.DOTALL
)


def _string_literals(node):
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _references_rowcount(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "rowcount":
            return True
        if isinstance(sub, ast.Call) and any(
            isinstance(a, ast.Constant) and a.value == "rowcount" for a in sub.args
        ):
            return True
    return False


def _has_rowcount_fail_closed(fn):
    """True when the function compares a rowcount against 0 (fail closed)."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        if not any(_references_rowcount(op) for op in operands):
            continue
        if any(isinstance(op, ast.Constant) and op.value == 0 for op in operands):
            return True
    return False


def _guarded_write_violations(site, fn, markers):
    """Structural violations for one registered guarded-write function."""
    strings = _string_literals(fn)
    violations = []
    if not any(_MUTATING_SQL_RE.match(s) for s in strings):
        violations.append(f"{site}: no mutating UPDATE/DELETE statement found")
    for marker in markers:
        if not any(marker in s for s in strings):
            violations.append(f"{site}: missing guard marker {marker!r}")
    if not _has_rowcount_fail_closed(fn):
        violations.append(f"{site}: missing rowcount fail-closed check")
    for s in strings:
        if _BARE_ID_WRITE_RE.match(s):
            violations.append(f"{site}: bare id-only write regressed: {s!r}")
    return violations


def test_guarded_write_pattern_present_at_registered_sites():
    """TOCTOU fixes: every registered check-then-write site keeps its guard."""
    assert _GUARDED_WRITE_SITES, "guarded-write registry is empty - guard is vacuous"
    for site, (rel_path, func_name, markers) in _GUARDED_WRITE_SITES.items():
        tree = _parse(rel_path)
        fn = _find_function(tree, func_name)
        assert fn is not None, f"{site}: {func_name} not found in {rel_path}"
        violations = _guarded_write_violations(site, fn, markers)
        assert not violations, "; ".join(violations)


def test_guarded_write_guard_flags_regressions():
    """Non-vacuity: checker passes a guarded write and flags regressions."""
    markers = ("AND owner_id = ?",)
    guarded = """
def guarded(db, user_id, server_id):
    cursor = db.execute(
        "UPDATE srv_servers SET deleted = 1 WHERE id = ? AND owner_id = ?",
        (server_id, user_id),
    )
    if getattr(cursor, "rowcount", 0) == 0:
        raise RuntimeError("concurrent change")
"""
    bare_write = """
def bare(db, server_id):
    cursor = db.execute(
        "UPDATE srv_servers SET deleted = 1 WHERE id = ?",
        (server_id,),
    )
    if getattr(cursor, "rowcount", 0) == 0:
        raise RuntimeError("concurrent change")
"""
    no_fail_closed = """
def no_guard(db, user_id, server_id):
    db.execute(
        "UPDATE srv_servers SET deleted = 1 WHERE id = ? AND owner_id = ?",
        (server_id, user_id),
    )
"""

    def _fn(src):
        return next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef))

    assert _guarded_write_violations("guarded", _fn(guarded), markers) == []
    bare_violations = _guarded_write_violations("bare", _fn(bare_write), markers)
    assert any("bare id-only write" in v for v in bare_violations), bare_violations
    no_fc_violations = _guarded_write_violations(
        "no_guard", _fn(no_fail_closed), markers
    )
    assert any("rowcount fail-closed" in v for v in no_fc_violations), no_fc_violations
