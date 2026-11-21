"""MESSAGING-GROUPDM-WRITE-SURFACE: close the insertion-guard's discovery gaps.

Companion to ``test_group_dm_insertion_gate_invariant.py`` (commit ``1f58425c``).
That guard enumerates callers of the two ``ParticipantService`` insertors and
checks each is gated, but its discovery is anchored on a single receiver name
(``self._participant_svc``) inside ``src/core/messaging``.  It therefore cannot
see an ungated write that arrives through any of these routes:

  * a raw ``INSERT INTO msg_participants`` issued elsewhere (new repository,
    admin repair route, migration helper),
  * a direct ``ParticipantRepository.create`` / ``create_bulk`` call that skips
    the ``ParticipantService`` insertors entirely,
  * a caller holding the same service under a different attribute/local name,
  * a caller living in a package outside ``src/core/messaging``.

This module derives the *entire* production write surface from source and
asserts a three-layer closure, so a new path at any layer fails the suite:

  L1  SQL writers: only ``ParticipantRepository.create`` / ``create_bulk``.
  L2  repo-insert callers: only the two ``ParticipantService`` insertors.
  L3  service-insertor callers (any receiver, whole production tree): only the
      five gated / documented-exception sites registered by the companion.

Every detector has a synthetic self-test that proves it flags the intended
pattern and rejects realistic look-alikes, so the guard cannot pass vacuously.
Pure static analysis: no app import, config, DB or fixture ordering.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

# The only low-level insertors (wrappers around the repository INSERT).
INSERTORS = frozenset({"add_participant", "add_user_to_multiple_conversations"})
# The repository methods that actually issue the INSERT statement.
REPO_INSERT_METHODS = frozenset({"create", "create_bulk"})

# The gated (or documented-exception) L3 callers.  Mirrors the companion
# guard's SITES registry but is checked with receiver-agnostic, repo-wide
# discovery so it cannot be bypassed by an alias receiver or a new package.
GATED_CALLERS = frozenset(
    {
        ("src/core/messaging/services/conversation.py", "create_group"),
        ("src/core/messaging/services/conversation.py", "create_dm"),
        ("src/core/messaging/services/conversation.py", "get_or_create_notes"),
        ("src/core/messaging/manager.py", "add_participant"),
        ("src/core/messaging/manager.py", "add_participant_to_conversations"),
    }
)

_SQL_WRITERS = {
    ("src/core/messaging/repositories/participant.py", "create"),
    ("src/core/messaging/repositories/participant.py", "create_bulk"),
}
_REPO_INSERT_CALLERS = {
    ("src/core/messaging/services/participant.py", "add_participant"),
    (
        "src/core/messaging/services/participant.py",
        "add_user_to_multiple_conversations",
    ),
}

# Known same-name, different-domain methods that are NOT participant insertors.
# Kept explicit and reviewable: a new look-alike must be justified here rather
# than silently escaping detection because of a receiver-name convention.
_NON_PARTICIPANT_INSERTOR_CALLS = frozenset(
    {
        # Voice-call participants live in voice-call tables, not
        # msg_participants; this is a VoiceCallManager.add_participant.
        ("src/core/voice/manager/calls.py", "_on_member_joined"),
    }
)


# --------------------------------------------------------------------------- #
# AST helpers (kept small and independently testable).
# --------------------------------------------------------------------------- #
def _iter_production_sources():
    """Every production Python source under ``src`` (tests excluded)."""
    for path in sorted(SRC.rglob("*.py")):
        parts = set(path.relative_to(ROOT).parts)
        if "tests" in parts or "__pycache__" in parts:
            continue
        yield path.relative_to(ROOT).as_posix(), path


def _anno_names(annotation):
    """Names/attributes referenced by an annotation node (incl. string forms)."""
    if annotation is None:
        return set()
    names = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _callee_name(call):
    """Bare name of a call target: ``f()`` -> 'f', ``x.f()`` -> 'f'."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _typed_attribute_names(tree, class_name):
    """Attribute/local names that hold a ``class_name`` instance in a module.

    Covers both direct construction (``self._x = ParticipantService(db)``) and
    dependency injection (``def __init__(self, svc: ParticipantService):
    self._x = svc``).  Receiver matching by *type* rather than by a hardcoded
    attribute name is what lets the detector see alias receivers.
    """
    annotated = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if node.args.vararg:
            args.append(node.args.vararg)
        if node.args.kwarg:
            args.append(node.args.kwarg)
        for arg in args:
            if class_name in _anno_names(arg.annotation):
                annotated.add(arg.arg)

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        is_instance = (
            isinstance(value, ast.Call) and _callee_name(value) == class_name
        ) or (isinstance(value, ast.Name) and value.id in annotated)
        if not is_instance:
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _call_owner(call):
    """Receiver name of a bound call: ``self._x.f()`` -> '_x', ``x.f()`` -> 'x'."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if isinstance(receiver, ast.Attribute):
        return receiver.attr
    if isinstance(receiver, ast.Name):
        return receiver.id
    return None


def _functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _own_calls(function):
    """Calls in ``function``'s own scope (nested defs/lambdas excluded)."""
    for child in ast.walk(function):
        if child is function:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Call):
            yield child


_INSERT_INTO_PARTICIPANTS = re.compile(
    r"(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO)\s+MSG_PARTICIPANTS"
)


def _has_direct_sql_insert(function, source):
    """Whether ``function`` itself issues an INSERT into msg_participants.

    A targeted statement pattern (not a loose "INSERT" + "msg_participants"
    conjunction) keeps a SELECT-on-participants / INSERT-elsewhere function
    from being misclassified as a participant writer.
    """
    segment = ast.get_source_segment(source, function) or ""
    return bool(_INSERT_INTO_PARTICIPANTS.search(segment.upper()))


def _calls_insertor(rel, function):
    """Whether ``function`` calls a participant insertor on any bound receiver.

    Receiver-agnostic on purpose: matching by a hardcoded attribute name (or
    even by an annotated type) would miss an injected service held under a new
    name.  Only explicitly allowlisted, different-domain look-alikes are
    excluded; a new one must be justified in ``_NON_PARTICIPANT_INSERTOR_CALLS``.
    """
    if (rel, function.name) in _NON_PARTICIPANT_INSERTOR_CALLS:
        return False
    for call in _own_calls(function):
        if _callee_name(call) in INSERTORS and _call_owner(call) is not None:
            return True
    return False


def _scan_write_surface():
    """Return (sql_writers, repo_insert_callers, insertor_callers)."""
    sql_writers, repo_callers, insertor_callers = set(), set(), set()
    for rel, path in _iter_production_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        repo_names = _typed_attribute_names(tree, "ParticipantRepository")
        for function in _functions(tree):
            if _has_direct_sql_insert(function, source):
                sql_writers.add((rel, function.name))
            if _calls_insertor(rel, function):
                insertor_callers.add((rel, function.name))
            for call in _own_calls(function):
                if (
                    _callee_name(call) in REPO_INSERT_METHODS
                    and _call_owner(call) in repo_names
                ):
                    repo_callers.add((rel, function.name))
    return sql_writers, repo_callers, insertor_callers


# --------------------------------------------------------------------------- #
# Non-vacuous self-tests: each detector must flag its target and reject
# realistic look-alikes.  These fail if the detector is weakened or narrowed.
# --------------------------------------------------------------------------- #
def test_detector_flags_raw_sql_insert_and_rejects_lookalikes():
    writer_src = "def repair(db, uid):\n    db.execute('INSERT OR IGNORE INTO msg_participants (id) VALUES (?)', (uid,))\n"
    assert _has_direct_sql_insert(next(_functions(ast.parse(writer_src))), writer_src)
    probe_src = textwrap.dedent(
        """
        def probe(db, cid):
            db.execute("SELECT 1 FROM msg_participants WHERE conversation_id = ?", (cid,))
            db.execute("INSERT INTO msg_other_table (id) VALUES (?)", (cid,))
        """
    )
    assert not _has_direct_sql_insert(next(_functions(ast.parse(probe_src))), probe_src)


def test_detector_flags_direct_repo_insert_only_for_participant_repo():
    src = textwrap.dedent(
        """
        class S:
            def __init__(self, db):
                self._repo = ParticipantRepository(db)
                self._other = AttachmentRepository(db)

            def build(self, rows):
                self._repo.create_bulk(rows)

            def unrelated(self, rows):
                self._other.create_bulk(rows)
        """
    )
    tree = ast.parse(src)
    repo_names = _typed_attribute_names(tree, "ParticipantRepository")
    assert "_repo" in repo_names
    assert "_other" not in repo_names
    hits = set()
    for fn in _functions(tree):
        for call in _own_calls(fn):
            if (
                _callee_name(call) in REPO_INSERT_METHODS
                and _call_owner(call) in repo_names
            ):
                hits.add(fn.name)
    assert hits == {"build"}


def test_detector_flags_insertor_under_any_receiver():
    """No annotation or receiver-name convention may hide an insertor call."""
    src = textwrap.dedent(
        """
        class S:
            def __init__(self, injected):
                self._anything = injected

            def aliased(self, cid, uid):
                self._anything.add_participant(cid, uid)

            def local_receiver(self, cid, uid):
                svc = self._anything
                svc.add_user_to_multiple_conversations(uid, [cid])

            def third_path(self):
                participant.add_participant(1, 2, 3)
        """
    )
    tree = ast.parse(src)
    rel = "src/core/messaging/services/widget.py"
    hits = {fn.name for fn in _functions(tree) if _calls_insertor(rel, fn)}
    # Every call site is a sighting, even one whose receiver merely *looks*
    # like a generic helper -- only explicit allowlist entries are excused.
    assert hits == {"aliased", "local_receiver", "third_path"}


def test_known_lookalike_is_allowlisted_not_silently_ignored():
    """The voice-call add_participant is excluded only via explicit allowlist."""
    src = textwrap.dedent(
        """
        def _on_member_joined(manager, call_id, uid):
            manager.add_participant(call_id, uid)
        """
    )
    fn = next(_functions(ast.parse(src)))
    assert _calls_insertor("src/core/voice/manager/calls.py", fn) is False
    # The identical call in any other module is a real, ungated sighting.
    assert _calls_insertor("src/core/other/calls.py", fn) is True


def test_detector_scans_the_whole_production_tree():
    rels = [rel for rel, _ in _iter_production_sources()]
    assert any(rel.startswith("src/api/") for rel in rels)
    assert any(rel.startswith("src/core/messaging/") for rel in rels)
    assert not any("/tests/" in rel for rel in rels)


# --------------------------------------------------------------------------- #
# Surface-closure invariants.
# --------------------------------------------------------------------------- #
def test_sql_write_surface_is_only_the_participant_repository():
    sql_writers, _, _ = _scan_write_surface()
    assert sql_writers == _SQL_WRITERS, (
        f"new raw SQL writer of msg_participants: {sorted(sql_writers - _SQL_WRITERS)}; "
        f"known writer missing: {sorted(_SQL_WRITERS - sql_writers)}"
    )


def test_repo_inserts_are_only_called_by_the_service_insertors():
    _, repo_callers, _ = _scan_write_surface()
    assert repo_callers == _REPO_INSERT_CALLERS, (
        f"new direct caller of a repo INSERT: {sorted(repo_callers - _REPO_INSERT_CALLERS)}; "
        f"known caller missing: {sorted(_REPO_INSERT_CALLERS - repo_callers)}"
    )


def test_every_insertor_caller_is_a_registered_gated_site():
    """Receiver-agnostic, repo-wide: no ungated insert path may exist."""
    _, _, insertor_callers = _scan_write_surface()
    assert insertor_callers == GATED_CALLERS, (
        f"ungated/unregistered insertor caller: {sorted(insertor_callers - GATED_CALLERS)}; "
        f"registered site missing: {sorted(GATED_CALLERS - insertor_callers)}"
    )
