"""Static guard: broad-swallow authorization gates must fail closed.

The classic fail-open this campaign removed looks like this::

    try:
        allowed = security_gate()          # raises / returns False on denial
    except Exception:
        logger.debug("gate unavailable")   # swallow, no deny
    perform_privileged_side_effect()       # grant / dispatch / write / deliver

Peers fixed the live instances: the DM broadcast block filter empties its
audience, the push-settings gates return 0, the artifact inline broadcast is
skipped when the gated send was rejected, the admin approval gate blanks its
row, and the permission memo degrades to the authoritative resolver. Runtime
tests cover those producers individually.

This guard has two halves:

1. The allowlist below reads production source directly with AST and asserts
   that every *documented* broad-swallow gate still carries its fail-closed
   marker. Deleting a marker (reintroducing swallow-then-continue) fails the
   suite even when no runtime fixture exercises that branch.

2. An allowlist is only as complete as its entries: on its own it cannot see a
   gate somebody adds later. The whole-tree sweep at the bottom therefore
   parses every production module under ``src/`` (excluding test and selftest
   scaffolding) and flags the *shape* those fixes removed -- a broad ``except``
   that absorbs the error while a value computed inside the ``try`` reaches a
   privileged side-effect call (dispatch/send/broadcast/emit/write/execute/...)
   afterwards, with no deny outcome on the error path. A new site must either
   fail closed or be recorded with a reviewed verdict; an unregistered hit
   fails the suite. The shape is deliberately narrow: a value merely logged or
   used by an inert call is not a grant, so ordinary best-effort cleanup code
   is not swept up.

Scope of the sweep: it flags sites where the swallowed value reaches a
privileged call in the *same* function (the direct dispatch/write form). A
gate that only returns the value for a caller to dispatch is covered by the
allowlist half above -- extending the sweep to call-graph flows would drag in
~100 benign "build a row/dict, return it, persist it" resolvers and their
allowlist would mask more than it catches. The two halves are complementary:
marker tampering on a documented gate fails half 1; a newly added direct
swallow-then-grant site fails half 2.

The self-tests prove both halves are non-vacuous: removing each marker,
injecting a fresh swallow-then-dispatch site (both the ``if allowed:`` guard
and the explicit-audience forms), and checking that fail-closed handlers are
not misflagged. Nothing is written to disk -- all mutations are in-memory
strings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Gate:
    """One documented broad-swallow-then-side-effect gate.

    ``fail_closed_marker`` must remain inside a broad ``except`` handler in
    ``func``; it is the token that turns an error into a denial/empty outcome
    instead of a grant.
    """

    name: str
    rel_path: str  # relative to SRC
    func: str
    fail_closed_marker: str
    why: str


# Documented allowlist of broad-swallow sites on authorization/audience gates.
# Each entry is safe *because* the named marker makes the error path deny.
DOCUMENTED_FAIL_CLOSED_GATES: tuple[Gate, ...] = (
    Gate(
        "DM broadcast block filter",
        "api/routes/messages/messages_crud/broadcast.py",
        "_resolve_recipients",
        "user_ids = []",
        "A failing two-way block filter must deliver to nobody rather than "
        "widening back to the unfiltered participant list.",
    ),
    Gate(
        "device push settings gate (sync)",
        "core/push/manager/delivery.py",
        "send_push",
        "return 0",
        "The settings lookup authorizes device delivery; an unreadable "
        "mobile_push opt-out must suppress, never grant, the push.",
    ),
    Gate(
        "device push settings gate (async)",
        "core/push/manager/delivery.py",
        "send_push_async",
        "return 0",
        "Same gate as the synchronous path; the async fan-out must fail "
        "closed when preferences cannot be resolved.",
    ),
    Gate(
        "artifact inline message broadcast",
        "api/routes/artifacts_helpers.py",
        "_emit_artifact_message",
        "send_rejected = True",
        "When the gated message send is rejected, its event must not be "
        "broadcast (no laundering a denied send into a visible message).",
    ),
    Gate(
        "admin approval gate",
        "api/routes/admin/utils.py",
        "require_approved_request",
        "gate_row = None",
        "A failed approval-state read must not enable the single-admin "
        "bypass or conjure an approval id; both lead to a 403.",
    ),
    Gate(
        "permission memo degradation",
        "core/servers/handlers/roles/permissions.py",
        "has_permission",
        "Fail closed to the authoritative resolver",
        "A broken channel-access memo must fall through to the authoritative "
        "computation, never synthesize a grant.",
    ),
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


@lru_cache(maxsize=None)
def _source(rel_path: str) -> str:
    return (SRC / rel_path).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _tree(rel_path: str) -> ast.Module:
    return ast.parse(_source(rel_path))


def _func_node(rel_path: str, name: str):
    for node in ast.walk(_tree(rel_path)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{rel_path}: function {name!r} not found")


def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name) and t.id in {"Exception", "BaseException"}:
        return True
    if isinstance(t, ast.Tuple) and any(
        isinstance(e, ast.Name) and e.id in {"Exception", "BaseException"}
        for e in t.elts
    ):
        return True
    return False


def _broad_handler_segments(rel_path: str, func: str) -> list[str]:
    """Source of every broad ``except`` handler inside ``func``."""
    src = _source(rel_path)
    segments: list[str] = []
    for node in ast.walk(_func_node(rel_path, func)):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if _is_broad(handler):
                segments.append(ast.get_source_segment(src, handler) or "")
    return segments


def _gate_fails_closed(handler_segments: list[str], marker: str) -> bool:
    """True when some broad handler still carries the deny marker."""
    return any(marker in seg for seg in handler_segments)


# Deny/empty tokens that make a swallowed error fail closed. Used only to prove
# the guard's classifier distinguishes a real fail-closed handler from a bare
# log-and-continue swallow (see the injection self-test).
_DENY_TOKENS = (
    "return False",
    "return None",
    "return 0",
    "return []",
    "return set()",
    "return {}",
    "= None",
    "= False",
    "= []",
    "= set()",
    "= {}",
    "continue",
    "raise",
    "sys.exit",
    "abort",
)


def _handler_is_fail_closed(segment: str) -> bool:
    return any(tok in segment for tok in _DENY_TOKENS)


def test_all_documented_gates_are_real():
    """Premise: every allowlist entry maps to a real function with a broad swallow."""
    for gate in DOCUMENTED_FAIL_CLOSED_GATES:
        segs = _broad_handler_segments(gate.rel_path, gate.func)
        assert segs, (
            f"{gate.rel_path}:{gate.func} no longer has any broad except "
            "handler; the documented gate premise changed -- re-audit it."
        )


def test_each_documented_gate_still_fails_closed():
    """Core assertion: no documented broad-swallow gate lost its deny marker."""
    for gate in DOCUMENTED_FAIL_CLOSED_GATES:
        segs = _broad_handler_segments(gate.rel_path, gate.func)
        assert _gate_fails_closed(segs, gate.fail_closed_marker), (
            f"{gate.name}: no broad handler in {gate.rel_path}:{gate.func} "
            f"still contains fail-closed marker {gate.fail_closed_marker!r}. "
            "A swallowed error could now continue to the privileged side "
            "effect (grant/dispatch/write/deliver) -- re-audit and restore "
            "the fail-closed outcome."
        )


def test_allowlist_entries_are_concrete():
    """Hygiene: each documented entry names a real file, function and reason."""
    seen = set()
    for gate in DOCUMENTED_FAIL_CLOSED_GATES:
        assert gate.rel_path.startswith("api/") or gate.rel_path.startswith("core/")
        assert (SRC / gate.rel_path).is_file(), gate.rel_path
        assert gate.func and gate.fail_closed_marker.strip()
        assert len(gate.why.strip()) > 25, gate.why
        key = (gate.rel_path, gate.func, gate.fail_closed_marker)
        assert key not in seen, f"duplicate gate entry: {key}"
        seen.add(key)


@pytest.mark.parametrize("gate", DOCUMENTED_FAIL_CLOSED_GATES, ids=lambda g: g.name)
def test_guard_flags_removed_fail_closed_marker(gate):
    """Non-vacuity: deleting a gate's deny marker must fail this guard.

    The mutation is in-memory only: strip the marker from the handler source
    and confirm the guard no longer sees a fail-closed broad handler.
    """
    segs = _broad_handler_segments(gate.rel_path, gate.func)
    assert _gate_fails_closed(segs, gate.fail_closed_marker)  # precondition

    weakened = [seg.replace(gate.fail_closed_marker, "pass") for seg in segs]
    assert not _gate_fails_closed(weakened, gate.fail_closed_marker), (
        f"removing {gate.fail_closed_marker!r} from {gate.name} was NOT flagged"
    )


def test_classifier_flags_fresh_log_only_swallow():
    """Non-vacuity: a new broad swallow that only logs is classified as risky.

    This is the exact shape the campaign removed (e.g. 'Push settings lookup
    failed, delivering anyway'); the classifier must not call it fail closed.
    """
    snippet = (
        "except Exception as e:\n"
        "    logger.debug(f'gate unavailable, continuing anyway: {e}')"
    )
    assert not _handler_is_fail_closed(snippet)
    # A real fail-closed handler from the tree is recognized as such.
    push = _broad_handler_segments("core/push/manager/delivery.py", "send_push")
    assert any(_handler_is_fail_closed(seg) for seg in push)


# --- Whole-tree completeness sweep ----------------------------------------
#
# The allowlist above guards the gates we already audited. It is blind to a
# gate added afterwards: nothing in the tests above ever looks at a function
# that is not named in DOCUMENTED_FAIL_CLOSED_GATES. The detector below closes
# that hole by sweeping every production module for the exact fail-open shape
# the campaign removed, so a NEW `except Exception: <allow>` site cannot pass
# silently just because nobody added it to the allowlist.

# Calls whose job is to expose/change the world. A swallowed error whose value
# reaches one of these is a potential grant/dispatch/write/deliver.
_PRIVILEGED_VERBS = (
    "dispatch",
    "broadcast",
    "emit",
    "send",
    "deliver",
    "notify",
    "publish",
    "grant",
    "approve",
    "perform",
    "execute",
    "persist",
    "write",
)

# Calls that deny outright even though they are calls, not `raise` statements
# (e.g. the routes' `raise_mapped(exc, ...)` helper).
_DENY_CALL_TOKENS = ("raise", "deny", "forbid", "abort")

# Empty-container constructors assigned in a handler are a deny/empty outcome
# (the audience becomes empty, so nobody receives the event).
_EMPTY_CONSTRUCTORS = frozenset({"set", "dict", "list", "frozenset"})


@dataclass(frozen=True)
class SwallowGrant:
    """A broad-swallow site whose absorbed value reaches a privileged call."""

    rel_path: str  # relative to SRC
    func: str
    side_call: str
    why: str = ""


# The only current whole-tree match, reviewed as safe: the replayed-operations
# list is pre-initialised to `[]`, so a swallowed replay failure leaves it
# empty and the sync carries no operations (no grant and no data leak).
KNOWN_SAFE_SWALLOW_GRANTS: tuple[SwallowGrant, ...] = (
    SwallowGrant(
        "api/websocket/handlers/sum.py",
        "handle_sum_subscribe",
        "send_sum_sync",
        "The swallowed value is the replayed-operations list, initialised to "
        "[] before the try. On a replay failure it stays empty, so the sync "
        "carries no operations; no audience or capability is widened.",
    ),
)


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return ""


def _reachable_nodes(root: ast.AST):
    """Yield ``root`` and nodes on its execution path, skipping nested defs.

    A nested function/lambda body does not run when the enclosing function
    runs, so an error absorbed there (or a dispatch living there) is not the
    same swallow-then-side-effect path. Mirrors the dead-guard fix in the
    SEC500/SEC100/200 guards.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _handler_absorbs_error(handler: ast.ExceptHandler) -> bool:
    """True when a broad handler swallows with no deny/empty outcome."""
    for node in _reachable_nodes(handler):
        if isinstance(node, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return False
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Constant) and value.value in (
                None,
                False,
                0,
                0.0,
                "",
            ):
                return False
            if isinstance(value, (ast.List, ast.Dict, ast.Set)) and not (
                getattr(value, "elts", None) or getattr(value, "keys", None)
            ):
                return False
            if (
                isinstance(value, ast.Call)
                and _call_name(value.func) in _EMPTY_CONSTRUCTORS
            ):
                return False
        if isinstance(node, ast.Call):
            name = _call_name(node.func).lower()
            if any(token in name for token in _DENY_CALL_TOKENS):
                return False
    return True


def _is_privileged_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and any(
        verb in _call_name(node.func).lower() for verb in _PRIVILEGED_VERBS
    )


def _call_uses(call: ast.Call, name: str) -> bool:
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Name) and sub.id == name:
                return True
    return False


def _first_privileged(node: ast.AST):
    for sub in _reachable_nodes(node):
        if _is_privileged_call(sub):
            return sub
    return None


def _statement_parents(root: ast.AST) -> dict:
    """Map each reachable statement to its (block, index)."""
    parents: dict = {}
    for node in _reachable_nodes(root):
        for field in ("body", "finalbody", "orelse"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                for index, stmt in enumerate(block):
                    parents[id(stmt)] = (block, index)
    return parents


def find_broad_swallow_grants(
    source: str, rel_path: str, tree=None
) -> list[SwallowGrant]:
    """Find swallow-then-privileged-side-effect sites in one module.

    A site is reported when a single function has (a) a ``try`` with a broad
    handler that absorbs the error, (b) an assignment from a call inside that
    ``try``, and (c) a privileged call in a statement *after* the ``try`` in
    the same block that consumes the assigned value -- either directly as an
    argument or as the test of an ``if`` guarding the call. Restricting (c) to
    the same block keeps the rule precise: the ~1500 unrelated best-effort
    ``except Exception: logger.debug(...)`` handlers in this tree never feed a
    swallowed value to a privileged call, so they are not reported.
    """
    tree = tree if tree is not None else ast.parse(source)
    found: list[SwallowGrant] = []
    for func in (
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parents = _statement_parents(func)
        own_tries = [n for n in _reachable_nodes(func) if isinstance(n, ast.Try)]
        for try_node in own_tries:
            if not any(
                _is_broad(h) and _handler_absorbs_error(h) for h in try_node.handlers
            ):
                continue

            assigned: set[str] = set()
            for stmt in try_node.body:
                for node in _reachable_nodes(stmt):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
                        node.value, ast.Call
                    ):
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        for target in targets:
                            if isinstance(target, ast.Name):
                                assigned.add(target.id)
            if not assigned:
                continue

            entry = parents.get(id(try_node))
            after = entry[0][entry[1] + 1 :] if entry else []
            after = after + list(try_node.orelse) + list(try_node.finalbody)

            side = None
            for stmt in after:
                privileged = _first_privileged(stmt)
                if privileged and any(_call_uses(privileged, v) for v in assigned):
                    side = privileged
                    break
            if side is None:
                # `try: allowed = gate() / except: log / if allowed: dispatch()`
                for stmt in after:
                    for node in _reachable_nodes(stmt):
                        if (
                            isinstance(node, ast.If)
                            and isinstance(node.test, ast.Name)
                            and node.test.id in assigned
                        ):
                            privileged = _first_privileged(node)
                            if privileged is not None:
                                side = privileged
                                break
                    if side is not None:
                        break
            if side is not None:
                found.append(SwallowGrant(rel_path, func.name, _call_name(side.func)))
    return found


@lru_cache(maxsize=1)
def _whole_tree_grants():
    """Run the detector over every production module under ``src/``."""
    grants: list[SwallowGrant] = []
    scanned = 0
    for path in sorted(SRC.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "selftest" in parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Fast prefilter: a Try needs both tokens. Avoids parsing ~500 modules.
        if "except" not in source or "try:" not in source:
            continue
        tree = ast.parse(source)
        scanned += 1
        rel = path.relative_to(SRC).as_posix()
        grants.extend(find_broad_swallow_grants(source, rel, tree))
    return tuple(grants), scanned


def _grant_key(grant: SwallowGrant) -> tuple[str, str, str]:
    return (grant.rel_path, grant.func, grant.side_call)


def test_no_unregistered_broad_swallow_grants():
    """Whole-tree lock: a new swallow-then-grant site cannot pass silently."""
    grants, scanned = _whole_tree_grants()
    assert scanned > 500, (
        f"whole-tree sweep parsed only {scanned} modules; the detector may "
        "have gone blind (prefilter/path regression)"
    )
    found = {_grant_key(g) for g in grants}

    allowed = {_grant_key(g) for g in KNOWN_SAFE_SWALLOW_GRANTS}
    unexpected = sorted(found - allowed)
    assert not unexpected, (
        "New broad-swallow grant(s) not in KNOWN_SAFE_SWALLOW_GRANTS:\n  "
        + "\n  ".join(
            f"{path}:{func} -> {side}(...)" for path, func, side in unexpected
        )
        + "\nThe except handler absorbs the error while a value from the try "
        "reaches a privileged call. Make the handler fail closed (deny/empty/"
        "raise), or record a reviewed verdict in the allowlist."
    )
    stale = sorted(allowed - found)
    assert not stale, (
        f"allowlisted swallow sites no longer present: {stale} -- re-audit the "
        "allowlist rather than leaving a stale entry that masks future hits"
    )


def test_whole_tree_sweep_is_not_vacuous():
    """The sweep must actually see the reviewed site on the current tree."""
    grants, _ = _whole_tree_grants()
    allowed = {_grant_key(g) for g in KNOWN_SAFE_SWALLOW_GRANTS}
    assert any(_grant_key(g) in allowed for g in grants), (
        "whole-tree sweep no longer sees any allowlisted site; the detector "
        "or its prefilter regressed and the completeness check is vacuous"
    )


def test_detector_flags_a_fresh_broad_swallow_grant():
    """Non-vacuity: injecting a new swallow-then-dispatch site is detected.

    Covers both shapes the campaign removed: an `if allowed:` guard on the
    absorbed predicate, and an explicit-audience list fed into a dispatch.
    """
    guarded = """
async def handler(event):
    try:
        allowed = check_gate()
    except Exception as exc:
        logger.debug("gate unavailable, continuing: %s", exc)
    if allowed:
        events.dispatch(event)
"""
    assert [g.side_call for g in find_broad_swallow_grants(guarded, "new.py")] == [
        "dispatch"
    ]

    explicit_audience = """
def fanout(event):
    try:
        user_ids = _dm_participants_without_blocks(user_ids, actor_id)
    except Exception as exc:
        logger.debug("block filter unavailable: %s", exc)
    events.dispatch(event, user_ids=user_ids)
"""
    assert [
        g.side_call for g in find_broad_swallow_grants(explicit_audience, "new.py")
    ] == ["dispatch"]


def test_detector_ignores_fail_closed_handlers():
    """Non-vacuity: a handler that denies is not mistaken for a grant."""
    for denied_body in (
        "        return 0",
        "        raise PermissionError('denied')",
        "        user_ids = []",
        "        raise_mapped(exc)",
        "        return",
    ):
        source = (
            "def fanout(event):\n"
            "    try:\n"
            "        user_ids = gate()\n"
            "    except Exception as exc:\n"
            "        logger.debug('gate failed: %s', exc)\n"
            f"{denied_body}\n"
            "    events.dispatch(event, user_ids=user_ids)\n"
        )
        assert find_broad_swallow_grants(source, "new.py") == [], denied_body
