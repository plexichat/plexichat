"""Structural guard: every retypeable recipient preference must use a semantic set.

Background
----------
Notification fan-out de-duplicates recipients with a ``if uid not in
mention_types`` guard, so when a message carries several mentions the first one
in the text assigns the recipient's stored ``MentionType``. Two recipient-side
preferences -- ``suppress_everyone`` and ``suppress_roles`` -- are applied in
``_should_notify_user_optimized`` by checking the *raw* ``mention_type``
(EVERYONE/HERE or ROLE). A member reached semantically by an @everyone-
equivalent mass ping (<#this-channel>, the default role, an all-member role)
or by a role mention can therefore be recorded as CHANNEL/EVERYONE/HERE/USER
and dodge the opt-out, leaking the content preview over websocket and push.

N670 (``374a595a``) and N791 (``63e9b7d3``) fixed this by tracking the semantic
audiences in ``mass_ping_recipients`` / ``role_ping_recipients`` and applying
the preferences to those sets in the fan-out loop *before* the raw-type gate.
Runtime tests cover the known bypasses, but nothing stops a later edit from
dropping a semantic-set gate, moving it after the raw gate, or adding a new
raw-type-keyed preference without a matching semantic set.

This is a pure AST ratchet (no app import/config/DB) over the committed HEAD
blob of ``creator.py`` (the file is contested by parallel agents, so the
working tree is deliberately ignored). It:

1. detects every preference applied inside a ``mention_type``-keyed ``if`` in
   ``_should_notify_user_optimized`` (the "retypeable" preferences);
2. requires each such preference to have a ``uid in SOME_SET and ...
   settings.pref ...: continue`` gate in ``create_notifications`` whose set is
   declared and populated, and that appears *before* the raw-type call;
3. pins the two campaign preferences to their named semantic sets;
4. proves non-vacuity with synthetic sources that add an uncovered
   raw-type-keyed preference and that move a gate after the raw call -- both
   must be rejected.

Run with::

    pytest -n 0 -q src/tests/notifications/test_notification_preference_semantic_guard.py
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit, pytest.mark.notifications]

ROOT = Path(__file__).resolve().parents[3]
CREATOR_REL = "src/core/notifications/manager/creator.py"

# The two campaign preferences and the semantic set each must be gated on.
CAMPAIGN = {
    "suppress_everyone": "mass_ping_recipients",
    "suppress_roles": "role_ping_recipients",
}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def creator_source() -> str:
    proc = _git("show", f"HEAD:{CREATOR_REL}")
    assert proc.returncode == 0, (
        f"cannot read {CREATOR_REL} from HEAD: {proc.stderr.strip()}"
    )
    return proc.stdout


# ---------------------------------------------------------------------------
# AST detector helpers
# ---------------------------------------------------------------------------


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _functions(src: str) -> dict[str, ast.AST]:
    tree = ast.parse(src)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _settings_prefs(node: ast.AST) -> set[str]:
    """``settings.<attr>`` reads anywhere under ``node``."""
    return {
        n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "settings"
    }


def _uses_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _retypeable_prefs(should_fn: ast.AST) -> set[str]:
    """Preferences gated on the raw ``mention_type`` in the decision function.

    These are exactly the ones a de-dup retype can bypass, so each needs a
    semantic-set gate in the fan-out loop.
    """
    prefs: set[str] = set()
    for node in ast.walk(should_fn):
        if isinstance(node, ast.If) and _uses_name(node.test, "mention_type"):
            prefs |= _settings_prefs(node)
    return prefs


def _semantic_gates(create_fn: ast.AST) -> dict[str, list[tuple[str, int]]]:
    """Map ``pref -> [(set_name, lineno)]`` for suppression guards in the loop.

    A gate is an ``if`` whose body contains a bare ``continue``, whose test
    reads ``settings.<pref>``, and whose test has ``uid in SOME_SET``.
    """
    gates: dict[str, list[tuple[str, int]]] = {}
    for node in ast.walk(create_fn):
        if not isinstance(node, ast.If):
            continue
        if not any(isinstance(stmt, ast.Continue) for stmt in node.body):
            continue
        prefs = _settings_prefs(node.test)
        if not prefs:
            continue
        sets: set[str] = set()
        for cmp in ast.walk(node.test):
            if (
                isinstance(cmp, ast.Compare)
                and any(isinstance(op, ast.In) for op in cmp.ops)
                and isinstance(cmp.left, ast.Name)
            ):
                for comparator in cmp.comparators:
                    if isinstance(comparator, ast.Name):
                        sets.add(comparator.id)
        for pref in prefs:
            for set_name in sets:
                gates.setdefault(pref, []).append((set_name, node.lineno))
    return gates


def _declared_sets(fn: ast.AST) -> set[str]:
    return {
        target.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _call_name(node.value) == "set"
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _populated_sets(fn: ast.AST) -> set[str]:
    return {
        node.func.value.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and _call_name(node) == "add"
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }


def _raw_gate_line(create_fn: ast.AST) -> int:
    lines = [
        node.lineno
        for node in ast.walk(create_fn)
        if isinstance(node, ast.Call)
        and _call_name(node) == "_should_notify_user_optimized"
    ]
    assert lines, "fan-out loop has no _should_notify_user_optimized call"
    return min(lines)


def _verify(src: str) -> None:
    fns = _functions(src)
    assert "create_notifications" in fns, "create_notifications missing from module"
    assert "_should_notify_user_optimized" in fns, (
        "decision function missing from module"
    )
    create_fn = fns["create_notifications"]
    should_fn = fns["_should_notify_user_optimized"]

    retypeable = _retypeable_prefs(should_fn)
    assert retypeable, "no raw-mention_type preference gate detected (guard is vacuous)"

    declared = _declared_sets(create_fn)
    populated = _populated_sets(create_fn)
    for pref, set_name in CAMPAIGN.items():
        assert pref in retypeable, f"{pref} is no longer a raw-type-keyed preference"
        assert set_name in declared, f"{set_name} no longer declared in the fan-out"
        assert set_name in populated, f"{set_name} no longer populated in the fan-out"

    gates = _semantic_gates(create_fn)
    raw_line = _raw_gate_line(create_fn)

    # Every retypeable preference needs a semantic-set gate ahead of the raw gate.
    for pref in retypeable:
        entries = gates.get(pref, [])
        assert entries, (
            f"{pref} is applied only via raw mention_type; no semantic-set gate found"
        )
        before = [(set_name, ln) for set_name, ln in entries if ln < raw_line]
        assert before, (
            f"{pref} semantic-set gate is not applied before the raw-type gate"
        )
        for set_name, _ln in before:
            assert set_name in declared and set_name in populated, (
                f"{pref} semantic gate uses undeclared/unpopulated set {set_name}"
            )

    # Pin the campaign preferences to their named sets, also before the raw gate.
    for pref, expected_set in CAMPAIGN.items():
        matching = [
            set_name
            for set_name, ln in gates.get(pref, [])
            if ln < raw_line and set_name == expected_set
        ]
        assert matching, (
            f"{pref} is not gated on semantic set {expected_set} before the raw-type gate"
        )


# ---------------------------------------------------------------------------
# The ratchet on committed HEAD + non-vacuity self-tests
# ---------------------------------------------------------------------------


def test_every_retypeable_preference_has_a_semantic_set_gate(creator_source):
    _verify(creator_source)


def test_guard_rejects_new_raw_type_preference_without_semantic_set(creator_source):
    """A future preference keyed only on mention_type must fail the guard."""
    needle = (
        "        if mention_type == MentionType.ROLE:\n"
        "            if settings.suppress_roles:\n"
        "                return False\n"
    )
    assert needle in creator_source, "cannot locate the raw ROLE gate to mutate"
    injected = needle + (
        "        if mention_type == MentionType.CHANNEL:\n"
        "            if settings.suppress_channels:\n"
        "                return False\n"
    )
    with pytest.raises(AssertionError, match="suppress_channels"):
        _verify(creator_source.replace(needle, injected, 1))


def test_guard_rejects_semantic_gate_placed_after_raw_gate():
    """A gate after the raw decision is too late -- the preview already leaked."""
    src = """
from enum import Enum

class MentionType(Enum):
    EVERYONE = 1
    ROLE = 2

class Manager:
    def create_notifications(self):
        mass_ping_recipients = set()
        role_ping_recipients = set()
        final = []
        for user_id, settings, mention_type in audience:
            mass_ping_recipients.add(user_id)
            role_ping_recipients.add(user_id)
            # Deliberately AFTER the raw-type decision.
            if self._should_notify_user_optimized(user_id, mention_type, settings):
                final.append(user_id)
            if user_id in mass_ping_recipients and settings and settings.suppress_everyone:
                continue
            if user_id in role_ping_recipients and settings and settings.suppress_roles:
                continue
        return final

    def _should_notify_user_optimized(self, user_id, mention_type, settings):
        if mention_type == MentionType.EVERYONE:
            if settings.suppress_everyone:
                return False
        if mention_type == MentionType.ROLE:
            if settings.suppress_roles:
                return False
        return True
"""
    with pytest.raises(AssertionError, match="before the raw-type gate"):
        _verify(src)
