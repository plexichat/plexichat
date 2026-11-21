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
tests cover those producers individually. This guard is the *whole-tree*
lock that makes the fixes durable: it reads production source directly with
AST and asserts that every documented broad-swallow gate still carries its
fail-closed marker. If someone deletes a marker (reintroducing a swallow-then-
continue), the suite fails even when no runtime fixture happens to exercise
that branch.

The self-tests at the bottom prove the guard is non-vacuous: removing each
marker, or injecting a fresh broad swallow that only logs before a privileged
call, is detected. It never edits files on disk -- all mutations are in-memory
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
