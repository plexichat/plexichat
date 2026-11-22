"""PERF700 invariant: bulk IN-clause queries bind ids in bounded chunks (<=500).

A fan-out (mentions, friend/role hydration, push batches, ...) can yield tens of
thousands of ids. The perf fix replaced the single ``IN (?, ?, ...)`` statement
with bounded slices so no query crosses SQLite's bind-variable cap and no read
issues one statement per id.

This is a *static* invariant: it parses the real modules with :mod:`ast` and
checks that each documented chunk gate is a small positive integer literal that
is actually wired into a ``range(0, len(...), GATE)`` chunking loop. No app
import, config, DB or network is involved, and every detector is first checked
against a synthetic violation before the real tree is trusted.

Marked ``unit`` (not ``performance``) so the default pytest selection keeps it.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
MAX_CHUNK = 500

# Module-level gate -> the exact name of its chunk-size constant. Curated to the
# bulk id/bind chunking fix sites; unrelated limits (bind caps of 900, byte
# chunks of 64 KiB, configured batch sizes, sticker cap of 400, ...) are not
# part of this contract and are deliberately excluded.
CHUNK_GATES = {
    "src/core/notifications/manager/settings.py": "_BULK_ID_CHUNK_SIZE",
    "src/core/notifications/manager/push.py": "_PUSH_BULK_CHUNK_SIZE",
    "src/core/relationships/manager/blocking.py": "_BULK_BLOCK_ID_CHUNK_SIZE",
    "src/core/relationships/manager/friends.py": "_BULK_FRIEND_ID_CHUNK_SIZE",
    "src/core/relationships/manager/mutual.py": "_BULK_SERVER_ID_CHUNK_SIZE",
    "src/core/messaging/services/message/retrieval.py": "_BULK_ID_CHUNK",
    "src/core/messaging/repositories/participant.py": "_MAX_BULK_CHUNK",
    "src/core/messaging/repositories/message_status.py": "_MAX_BULK_CHUNK",
    "src/core/automod/manager/evaluation.py": "_BULK_LOOKUP_CHUNK",
    "src/core/voice/manager/base.py": "_BULK_ID_CHUNK_SIZE",
    "src/core/servers/manager/onboarding_ops.py": "_ROLE_LOOKUP_CHUNK_SIZE",
    "src/core/servers/manager/member_count_cache.py": "_BATCH_CHUNK",
    "src/core/settings/manager.py": "_BULK_KEY_CHUNK_SIZE",
    "src/core/presence/manager/presence.py": "_PRESENCE_BULK_CHUNK_SIZE",
    "src/core/auth/repositories/user_notes.py": "MAX_NOTES_READ_CHUNK",
    "src/api/websocket/handlers/guild.py": "_BULK_LOOKUP_CHUNK_SIZE",
    "src/api/routes/admin/audit.py": "_AUDIT_EXPORT_CHUNK_SIZE",
    "src/api/routes/admin/moderation.py": "_BULK_USER_ID_CHUNK_SIZE",
    "src/api/routes/messages/helpers.py": "_MAX_EMBED_SUPPRESSION_CHUNK",
    "src/core/search/manager/access.py": "_BIND_CHUNK",
}

# Gates confirmed to be the *step* of a ``range(0, len(...), GATE)`` loop (not
# merely a default argument). These must demonstrably drive the chunking.
RANGE_STEP_GATES = {
    "src/core/notifications/manager/settings.py": "_BULK_ID_CHUNK_SIZE",
    "src/core/notifications/manager/push.py": "_PUSH_BULK_CHUNK_SIZE",
    "src/core/relationships/manager/blocking.py": "_BULK_BLOCK_ID_CHUNK_SIZE",
    "src/core/relationships/manager/friends.py": "_BULK_FRIEND_ID_CHUNK_SIZE",
    "src/core/relationships/manager/mutual.py": "_BULK_SERVER_ID_CHUNK_SIZE",
    "src/core/messaging/services/message/retrieval.py": "_BULK_ID_CHUNK",
    "src/core/messaging/repositories/participant.py": "_MAX_BULK_CHUNK",
    "src/core/automod/manager/evaluation.py": "_BULK_LOOKUP_CHUNK",
    "src/core/voice/manager/base.py": "_BULK_ID_CHUNK_SIZE",
    "src/core/servers/manager/onboarding_ops.py": "_ROLE_LOOKUP_CHUNK_SIZE",
    "src/core/servers/manager/member_count_cache.py": "_BATCH_CHUNK",
    "src/core/settings/manager.py": "_BULK_KEY_CHUNK_SIZE",
    "src/core/presence/manager/presence.py": "_PRESENCE_BULK_CHUNK_SIZE",
    "src/api/websocket/handlers/guild.py": "_BULK_LOOKUP_CHUNK_SIZE",
    "src/api/routes/admin/moderation.py": "_BULK_USER_ID_CHUNK_SIZE",
    "src/api/routes/messages/helpers.py": "_MAX_EMBED_SUPPRESSION_CHUNK",
    "src/core/search/manager/access.py": "_BIND_CHUNK",
}


def _parse(source):
    return ast.parse(textwrap.dedent(source))


def _module_constant(tree, name):
    """AST value node of a module-level ``name = ...`` assignment."""
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _int_literal(node):
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    return None


def _load_count(tree, name):
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
    )


def _uses_as_range_step(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "range"
            and len(node.args) == 3
        ):
            if any(isinstance(a, ast.Name) and a.id == name for a in node.args):
                return True
    return False


def _check_gate_source(source, name):
    """Problems with the bounded value of ``name`` in ``source``."""
    tree = _parse(source)
    node = _module_constant(tree, name)
    if node is None:
        return [f"{name}: missing module-level constant"]
    problems = []
    value = _int_literal(node)
    if value is None:
        problems.append(f"{name}: not an integer literal")
    elif value < 1:
        problems.append(f"{name}: non-positive value {value}")
    elif value > MAX_CHUNK:
        problems.append(f"{name}: {value} > {MAX_CHUNK}")
    if _load_count(tree, name) < 1:
        problems.append(f"{name}: defined but never used")
    return problems


def _check_range_step_source(source, name):
    """Problems with the ``range(..., GATE)`` wiring of ``name``."""
    tree = _parse(source)
    if _module_constant(tree, name) is None:
        return [f"{name}: missing module-level constant"]
    if not _uses_as_range_step(tree, name):
        return [f"{name}: not used as a range step"]
    return []


def _real_problems(checker, gates):
    problems = []
    scanned = []
    for rel, name in sorted(gates.items()):
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing file")
            continue
        source = path.read_text(encoding="utf-8")
        problems.extend(f"{rel}: {p}" for p in checker(source, name))
        scanned.append(rel)
    return problems, scanned


_VALUE_PROBLEMS, _VALUE_SCANNED = _real_problems(_check_gate_source, CHUNK_GATES)
_STEP_PROBLEMS, _STEP_SCANNED = _real_problems(
    _check_range_step_source, RANGE_STEP_GATES
)


def test_self_test_detectors_catch_synthetic_violations():
    """Both detectors reject synthetic oversized/unwired chunk gates."""
    good = textwrap.dedent(
        """
        _BULK_ID_CHUNK_SIZE = 500

        def _chunks(items):
            for start in range(0, len(items), _BULK_ID_CHUNK_SIZE):
                yield items[start : start + _BULK_ID_CHUNK_SIZE]
        """
    )
    oversized = textwrap.dedent(
        """
        _BULK_ID_CHUNK_SIZE = 5000

        def _chunks(items):
            for start in range(0, len(items), _BULK_ID_CHUNK_SIZE):
                yield items[start : start + _BULK_ID_CHUNK_SIZE]
        """
    )
    unwanted = "_BULK_ID_CHUNK_SIZE = 500\n"
    missing = "def _chunks(items):\n    return items\n"
    unwired = textwrap.dedent(
        """
        _BULK_ID_CHUNK_SIZE = 500

        def _chunks(items):
            return [items]
        """
    )

    assert _check_gate_source(good, "_BULK_ID_CHUNK_SIZE") == []
    assert any(
        "> 500" in p for p in _check_gate_source(oversized, "_BULK_ID_CHUNK_SIZE")
    )
    assert any(
        "never used" in p for p in _check_gate_source(unwanted, "_BULK_ID_CHUNK_SIZE")
    )
    assert any(
        "missing" in p for p in _check_gate_source(missing, "_BULK_ID_CHUNK_SIZE")
    )

    assert _check_range_step_source(good, "_BULK_ID_CHUNK_SIZE") == []
    assert any(
        "range step" in p
        for p in _check_range_step_source(unwired, "_BULK_ID_CHUNK_SIZE")
    )


def test_scan_is_not_vacuous():
    assert len(CHUNK_GATES) >= 15
    assert len(RANGE_STEP_GATES) >= 12
    assert len(_VALUE_SCANNED) == len(CHUNK_GATES)
    assert len(_STEP_SCANNED) == len(RANGE_STEP_GATES)


def test_all_batch_chunk_gates_are_bounded_at_500():
    assert not _VALUE_PROBLEMS, (
        "batch chunk gates drifted above the 500-id budget:\n  "
        + "\n  ".join(sorted(_VALUE_PROBLEMS))
    )


def test_batch_chunk_gates_drive_the_chunking_loop():
    assert not _STEP_PROBLEMS, (
        "batch chunk gates are no longer wired into range() chunking:\n  "
        + "\n  ".join(sorted(_STEP_PROBLEMS))
    )
