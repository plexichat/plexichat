"""Guard the per-column ``UPDATE`` builders against duplicate assignments.

Seven builders in the servers system update one column per statement:
``update_server``, ``update_channel``, ``update_member``, ``update_role``,
``update_scheduled_event``, ``update_template`` and
``update_onboarding_step``. Each statement also writes ``updated_at``, so
listing ``updated_at = ?`` in the builder's ``updates`` list as well produces
``SET updated_at = ?, updated_at = ?`` -- accepted by SQLite, rejected by
PostgreSQL (see ``test_duplicate_set_assignment_regression.py``).

Two layers of defence, because they catch different mistakes:

* the runtime guard below drives every builder and inspects the SQL it really
  executed, so a wrong branch or a new statement is caught immediately;
* the static audit parses the source of every per-column builder and fails if
  one of them both lists ``updated_at`` in its assignment list *and* writes
  ``updated_at`` in the statement text, which is the exact shape both shipped
  defects had. It needs no fixtures, so a newly added builder is covered the
  moment it is written.
"""

import ast
import textwrap
import time
import uuid
from pathlib import Path

from src.tests._conftest.sql_guard import (
    assert_no_duplicate_assignments,
    capture_executed_sql,
    update_statements,
)

SRC_DIR = Path(__file__).resolve().parents[2]
UPDATED_AT_ASSIGNMENT = "updated_at = ?"
# A statement template that writes updated_at *after* another assignment.
UPDATED_AT_AFTER_COLUMN = ", " + UPDATED_AT_ASSIGNMENT


def _normalise_sql(sql: str) -> str:
    """Collapse whitespace so multi-line statement templates still match."""
    return " ".join(sql.split())


def _assert_builder_is_clean(executor, action, label):
    """Run ``action`` and assert none of its UPDATEs assigns a column twice."""
    statements = capture_executed_sql(executor, action)
    assert update_statements(statements), f"{label} issued no UPDATE statement"
    assert_no_duplicate_assignments(statements)


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def test_update_server_never_assigns_a_column_twice(server_with_members):
    """Two changed columns mean two statements, each single-assignment."""
    server, owner, *_rest, servers = server_with_members

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_server(
            user_id=owner.id,
            server_id=server.id,
            name="Renamed",
            max_reactions_per_message=25,
        ),
        "update_server",
    )


def test_update_channel_never_assigns_a_column_twice(server_with_members):
    server, owner, *_rest, servers = server_with_members
    channel = servers.create_channel(owner.id, server.id, _unique("chan"))

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_channel(
            owner.id, channel.id, name=_unique("chan"), topic="topic"
        ),
        "update_channel",
    )


def test_update_member_never_assigns_a_column_twice(server_with_members):
    server, owner, _, member_user, *_rest, servers = server_with_members

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_member(
            owner.id, server.id, member_user.id, nickname="nick", muted=True
        ),
        "update_member",
    )


def test_update_role_never_assigns_a_column_twice(server_with_members):
    _, owner, _, _, _, admin_role, servers = server_with_members

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_role(
            user_id=owner.id, role_id=admin_role.id, name="Renamed Role"
        ),
        "update_role",
    )


def test_update_scheduled_event_never_assigns_a_column_twice(server_with_members):
    server, owner, *_rest, servers = server_with_members
    event = servers.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name="Standup",
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=servers.ScheduledEventType.EXTERNAL,
        location="Online",
    )

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_scheduled_event(
            user_id=owner.id, event_id=event.id, name="Standup 2", location="Office"
        ),
        "update_scheduled_event",
    )


def test_update_template_never_assigns_a_column_twice(server_with_members):
    server, owner, *_rest, servers = server_with_members
    template = servers.create_template(owner.id, server.id, _unique("tpl"))

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_template(
            owner.id, template.code, name=_unique("tpl"), is_public=True
        ),
        "update_template",
    )


def test_update_onboarding_step_never_assigns_a_column_twice(server_with_members):
    server, owner, *_rest, servers = server_with_members
    step = servers.create_onboarding_step(
        owner.id,
        server.id,
        servers.OnboardingStepType.READ_RULES,
        _unique("Rules"),
    )

    _assert_builder_is_clean(
        servers._db,
        lambda: servers.update_onboarding_step(
            owner.id, step.id, title=_unique("Rules"), position=2
        ),
        "update_onboarding_step",
    )


def _iter_source_files():
    for path in sorted(SRC_DIR.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


def _updated_at_list_entries(function: ast.AST) -> set:
    """Ids of the literal ``updated_at = ?`` nodes the function lists.

    Covers both ``updates.append("updated_at = ?")`` and an initialising list
    literal (``updates = ["updated_at = ?"]``). The ids are returned so the
    SQL-literal scan below can ignore exactly these nodes: the append argument
    is itself a string constant, and counting it as a statement would flag
    every builder that lists the entry, including the correct single-statement
    ones that join the list.
    """
    entries = set()
    for node in ast.walk(function):
        candidates = []
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "insert")
        ):
            candidates = list(node.args)
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            candidates = [node.value] if isinstance(node, ast.Assign) else []
            if isinstance(node, ast.Assign):
                candidates = (
                    [value for value in node.value.elts]
                    if isinstance(node.value, (ast.List, ast.Tuple))
                    else []
                )
        for candidate in candidates:
            if (
                isinstance(candidate, ast.Constant)
                and candidate.value == UPDATED_AT_ASSIGNMENT
            ):
                entries.add(id(candidate))
            elif isinstance(candidate, (ast.List, ast.Tuple)):
                for element in candidate.elts:
                    if (
                        isinstance(element, ast.Constant)
                        and element.value == UPDATED_AT_ASSIGNMENT
                    ):
                        entries.add(id(element))
    return entries


def _emits_updated_at_after_another_column(function: ast.AST, excluded: set) -> bool:
    """True if a statement template assigns another column *and* ``updated_at``.

    The signature of the defect is a statement text shaped like
    ``SET <column> = ?, updated_at = ?`` -- updated_at written after another
    assignment -- which is exactly ``", updated_at = ?"`` once whitespace is
    normalised. A standalone ``SET updated_at = ?`` does not match: that is
    the legitimate "touch the timestamp" statement, and a join-style builder
    that emits it alongside a list of assignments is correct.

    ``excluded`` holds the assignment-list entries found by
    :func:`_updated_at_list_entries`; those constants are data for a list, not
    statement text, and matching them would flag every builder that mentions
    the column.
    """
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in excluded
        ):
            if UPDATED_AT_AFTER_COLUMN in _normalise_sql(node.value):
                return True
    return False


def _offenders_in_source(source: str, label: str) -> list:
    """Names of functions in ``source`` that carry the defect shape."""
    offenders = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        list_entries = _updated_at_list_entries(function)
        if list_entries and _emits_updated_at_after_another_column(
            function, list_entries
        ):
            offenders.append(f"{label}::{function.name} (line {function.lineno})")
    return offenders


def test_static_audit_recognises_the_shipped_defect_shapes():
    """The audit must catch both real shapes and clear correct neighbours.

    The two shipped defects were built differently -- one concatenated the
    column into a template (``... + column + " = ?, updated_at = ?"``), the
    other spelled each column out in its own if/elif statement -- so both are
    pinned here, together with the two correct patterns they must not be
    confused with.
    """
    templated = textwrap.dedent("""
        def update_thing(self, column):
            updates = []
            params = []
            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if updates:
                updates.append("updated_at = ?")
                params.append(1)
                now = 2
                for column in updates:
                    query = (
                        "UPDATE t SET "
                        + column
                        + " = ?, updated_at = ? WHERE id = ?"
                    )
                    self._db.execute(query, (now,))
    """)
    hardcoded = textwrap.dedent("""
        def update_thing(self):
            updates = []
            if name is not None:
                updates.append("name = ?")
            if updates:
                updates.append("updated_at = ?")
                params.append(1)
                if "name = ?" in updates:
                    self._db.execute(
                        "UPDATE t SET name = ?, updated_at = ? WHERE id = ?"
                    )
    """)
    joined = textwrap.dedent("""
        def update_thing(self):
            sets = ["updated_at = ?"]
            if name is not None:
                sets.append("name = ?")
            self._db.execute(f"UPDATE t SET {', '.join(sets)} WHERE id = ?")
    """)
    timestamp_only = textwrap.dedent("""
        def update_thing(self):
            sets = []
            if name is not None:
                sets.append("name = ?")
            if not sets:
                self._db.execute("UPDATE t SET updated_at = ? WHERE id = ?")
            else:
                sets.append("updated_at = ?")
                self._db.execute(f"UPDATE t SET {', '.join(sets)} WHERE id = ?")
    """)

    assert _offenders_in_source(templated, "synthetic") == [
        "synthetic::update_thing (line 2)"
    ], "the concatenated-template shape must be detected"
    assert _offenders_in_source(hardcoded, "synthetic") == [
        "synthetic::update_thing (line 2)"
    ], "the per-column if/elif shape must be detected"
    assert _offenders_in_source(joined, "synthetic") == []
    assert _offenders_in_source(timestamp_only, "synthetic") == []


def test_no_builder_lists_updated_at_while_also_writing_it():
    """Static audit of the defect class across ``src``.

    A builder that emits ``SET <column> = ?, updated_at = ?`` and *also* keeps
    ``updated_at = ?`` in the list of assignments it iterates over ends up
    assigning the column twice for that entry. Both shipped defects had exactly
    this shape, and it is invisible without a PostgreSQL backend, so it is
    checked statically to cover builders no fixture happens to exercise.
    """
    offenders = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        # Fast filter: only parse files that might be update builders touching updated_at
        if "updated_at" not in text:
            continue
        if "update_" not in text and "UPDATE" not in text:
            continue
        offenders.extend(_offenders_in_source(text, str(path.relative_to(SRC_DIR))))

    assert offenders == [], (
        "These builders both list 'updated_at = ?' in their assignment list and "
        "write updated_at in the statement text, so the entry emits "
        "'SET updated_at = ?, updated_at = ?' (PostgreSQL error 42601). Drop the "
        "list entry -- every statement already writes updated_at: "
        + ", ".join(offenders)
    )
