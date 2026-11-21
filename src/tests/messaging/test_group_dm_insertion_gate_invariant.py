"""MESSAGING-GROUPDM-PRIVACY invariant: every participant-insertion path is gated.

FINAL sweep of group-DM participant mutation. The policy surface was reduced to
two low-level insertors (the only production code that writes ``msg_participants``):

  * ``ConversationService.create_dm`` and ``ParticipantService.add_participant``
    via ``ParticipantRepository.create``;
  * ``ParticipantService.add_user_to_multiple_conversations`` via
    ``ParticipantRepository.create_bulk``.

The repository layer is intentionally policy-free (it just issues the INSERT);
the privacy decision belongs to the *callers* of those two insertors. This is a
static AST lock, modelled on the other ``*_invariant`` tests in this tree: it
needs no app import, config, DB or fixture ordering and cannot flake.

It fails if:

  * a NEW production caller of either insertor appears without being added to
    the enumerated ``SITES`` registry below (the discovery/non-vacuity test),
  * a gated site stops calling its gate,
  * a documented exception loses the inline privacy markers that justify it,
  * either privacy gate is weakened past its fail-closed markers.

All five insertion sites are enumerated with a concrete justification. Three
call the shared gates (``create_group`` all-pairs, ``add_participant`` symmetric,
``add_participant_to_conversations`` batch); two are documented exceptions with
their own inline gate (``create_dm``) or a single self-participant (``notes``).
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

ROOT = Path(__file__).resolve().parents[3]

# The two low-level participant insertors. Discovery looks for calls to these
# names on the ``self._participant_svc`` receiver, which is the only receiver
# used for them in production messaging code. Voice calls, module-level
# delegators and server handlers use different receivers and are deliberately
# out of scope: they delegate to the gated manager methods below.
INSERTOR_NAMES = frozenset({"add_participant", "add_user_to_multiple_conversations"})
PARTICIPANT_SVC_RECEIVER = "_participant_svc"

# (source path relative to ROOT, function name, accepted gate call names,
#  required inline markers for documented exceptions, justification).
# ``accepted_gates`` empty means a documented exception; ``markers`` must then
# be non-empty so the exemption cannot silently widen.
SITES = [
    (
        "src/core/messaging/services/conversation.py",
        "create_group",
        {"enforce_group_dm_privacy"},
        (),
        "all-pairs group-DM gate (owner<->each and each<->each, both directions)",
    ),
    (
        "src/core/messaging/manager.py",
        "add_participant",
        {"enforce_group_dm_privacy"},
        (),
        "symmetric gate vs every existing member before single add",
    ),
    (
        "src/core/messaging/manager.py",
        "add_participant_to_conversations",
        {"enforce_conversation_dm_privacy"},
        (),
        "batch gate for group DMs; server-channel GROUP rows skipped by design",
    ),
    (
        "src/core/messaging/services/conversation.py",
        "create_dm",
        set(),
        ("is_blocked_by_either", "allow_dms_from", "rel_blocked"),
        "2-party DM: inline block + recipient-settings gate, fail-closed fallback",
    ),
    (
        "src/core/messaging/services/conversation.py",
        "get_or_create_notes",
        set(),
        ("max_participants=1", "ParticipantRole.OWNER"),
        "NOTES conversation: sole self-participant, no other party to vet",
    ),
]

# Fail-closed markers that must survive inside each shared gate.
GATES = {
    (
        "src/core/messaging/services/conversation.py",
        "enforce_group_dm_privacy",
    ): (
        "is_blocked_by_either",
        "rel_blocked",
        "allow_dms_from",
        "friends",
        "(participant_id, initiator_id)",
    ),
    (
        "src/core/messaging/services/conversation.py",
        "enforce_conversation_dm_privacy",
    ): (
        "get_participant_ids",
        "server_id",
        "ConversationType.GROUP.value",
        "enforce_group_dm_privacy",
    ),
}


def _parse(rel_path):
    return ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _called_names(node):
    """All called names in ``node`` as (bare-name, attribute-name) pairs."""
    bare, attrs = set(), set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if isinstance(func, ast.Name):
            bare.add(func.id)
        elif isinstance(func, ast.Attribute):
            attrs.add(func.attr)
    return bare, attrs


def _is_insertor_call(node):
    """Whether ``node`` calls an insertor on ``self._participant_svc``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in INSERTOR_NAMES:
        return False
    receiver = func.value
    return (
        isinstance(receiver, ast.Attribute)
        and receiver.attr == PARTICIPANT_SVC_RECEIVER
    )


def _discover_insertion_callsites():
    """Return {(rel_path, function)} for every production insertor caller."""
    found = set()
    for path in sorted((ROOT / "src/core/messaging").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Only calls in this function's own scope (exclude nested defs so a
            # helper does not attribute its insert to the enclosing function).
            own_calls = []
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                ):
                    continue
                own_calls.append(child)
            if any(_is_insertor_call(call) for call in own_calls):
                found.add((rel, node.name))
    return found


def test_detector_catches_insertor_calls():
    """Self-test: the detector flags real insertor calls, not look-alikes."""
    good = textwrap.dedent(
        """
        class S:
            def build(self, uid, cid):
                self._participant_svc.add_participant(cid, uid, role)
                return self._repo.create(cid, uid)
        """
    )
    bad = textwrap.dedent(
        """
        class S:
            def lookalike(self):
                # voice call manager / module delegator / repo: NOT insertors.
                manager.add_participant(call.id, user_id)
                _get_manager().add_participant(a, b, c)
                self._repo.create(a, b, c, d, e)
        """
    )
    for src, expected in ((good, True), (bad, False)):
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        hits = [n for n in ast.walk(fn) if n is not fn and _is_insertor_call(n)]
        assert bool(hits) is expected, src


def test_registry_is_definitive():
    """Every production insertor caller is enumerated; no silent new path."""
    discovered = _discover_insertion_callsites()
    registered = {(rel, fn) for rel, fn, *_ in SITES}
    assert discovered == registered, (
        f"unregistered insertor call sites (add a gate + SITES entry): "
        f"{sorted(discovered - registered)}; "
        f"stale SITES entries: {sorted(registered - discovered)}"
    )


@pytest.mark.parametrize("rel,fn,gates,markers,why", SITES)
def test_insertion_site_is_gated(rel, fn, gates, markers, why):
    """Each site either calls its gate or keeps its documented inline gate."""
    node = _find_function(_parse(rel), fn)
    assert node is not None, f"insertion site {rel}:{fn} disappeared"
    bare, attrs = _called_names(node)
    called = bare | attrs
    if gates:
        assert called & gates, (
            f"{rel}:{fn} no longer calls any of {sorted(gates)} ({why})"
        )
    else:
        source = ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), node)
        assert source is not None, f"cannot read source of {rel}:{fn}"
        missing = [m for m in markers if m not in source]
        assert not missing, (
            f"{rel}:{fn} documented exception lost inline marker(s) {missing} ({why})"
        )


@pytest.mark.parametrize(
    "rel,fn,expected_markers",
    [(rel, fn, markers) for (rel, fn), markers in GATES.items()],
)
def test_privacy_gates_stay_fail_closed(rel, fn, expected_markers):
    """A gate simplified past its fail-closed markers is a regression."""
    node = _find_function(_parse(rel), fn)
    assert node is not None, f"privacy gate {rel}:{fn} disappeared"
    source = ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), node)
    assert source is not None
    missing = [m for m in expected_markers if m not in source]
    assert not missing, f"{rel}:{fn} lost fail-closed marker(s): {missing}"
