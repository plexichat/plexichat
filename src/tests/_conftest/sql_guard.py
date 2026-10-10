"""Shared assertions about the SQL a code path actually executes.

These exist because of a defect class that SQLite hides: an ``UPDATE`` builder
that assigns the same column twice. SQLite accepts
``SET updated_at = ?, updated_at = ?`` (the last assignment silently wins) and
the test suite stays green, while PostgreSQL rejects the statement outright
with ``multiple assignments to same column "updated_at"`` -- so every such
update failed on a PostgreSQL deployment. Two builders were affected (role and
scheduled-event updates); both are covered by the guards that use this module.

The helpers are backend-agnostic and fixture-free so any test can assert on
generated SQL, and the parsing is quote- and paren-aware so an assignment
whose *value* contains a comma or an ``=`` (``f(a, b)``, ``'x=y'``) is not
mistaken for a second column.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List

# The SET clause ends at the first top-level occurrence of one of these.
_CLAUSE_END_KEYWORDS = ("WHERE", "RETURNING", "FROM", "ORDER", "LIMIT")


def _is_word_at(text: str, index: int, length: int) -> bool:
    """True when ``text[index:index + length]`` is a standalone word."""
    before = text[index - 1] if index > 0 else " "
    after_index = index + length
    after = text[after_index] if after_index < len(text) else " "
    return not (before.isalnum() or before == "_") and not (
        after.isalnum() or after == "_"
    )


def _find_top_level_keyword(text: str, keyword: str, start: int = 0) -> int:
    """Index of ``keyword`` outside quotes and parentheses, or -1.

    Scanning stops at the first match, and quoted literals (single or double)
    are skipped whole so a keyword appearing inside a string value is ignored.
    """
    upper = text.upper()
    target_length = len(keyword)
    depth = 0
    i = start
    length = len(text)
    while i < length:
        char = text[i]
        if char == "'":
            i += 1
            while i < length:
                if text[i] == "'":
                    if i + 1 < length and text[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if char == '"':
            i += 1
            while i < length and text[i] != '"':
                i += 1
            i += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif (
            depth == 0
            and upper.startswith(keyword, i)
            and _is_word_at(text, i, target_length)
        ):
            return i
        i += 1
    return -1


def _split_top_level(text: str) -> List[str]:
    """Split on commas that are outside quotes and parentheses."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char == "'":
            current.append(char)
            i += 1
            while i < length:
                current.append(text[i])
                if text[i] == "'":
                    if i + 1 < length and text[i + 1] == "'":
                        current.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if char == '"':
            current.append(char)
            i += 1
            while i < length and text[i] != '"':
                current.append(text[i])
                i += 1
            if i < length:
                current.append('"')
                i += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


def assigned_columns(sql: str) -> List[str]:
    """Columns the statement assigns in its SET clause, in written order.

    Returns an empty list for a statement with no SET clause. Column names are
    lower-cased and stripped of quoting and table qualifiers so ``t."Name"``
    and ``name`` compare equal.
    """
    set_index = _find_top_level_keyword(sql, "SET")
    if set_index == -1:
        return []

    end = len(sql)
    for keyword in _CLAUSE_END_KEYWORDS:
        position = _find_top_level_keyword(sql, keyword, set_index + 3)
        if position != -1:
            end = min(end, position)

    columns: List[str] = []
    for part in _split_top_level(sql[set_index + 3 : end]):
        if "=" not in part:
            continue
        name = part.split("=", 1)[0].strip().strip('"').strip("`").strip()
        if "." in name:
            name = name.split(".", 1)[1]
        if name:
            columns.append(name.lower())
    return columns


def duplicate_set_columns(sql: str) -> List[str]:
    """Columns assigned more than once by a single statement."""
    counts: dict = {}
    for column in assigned_columns(sql):
        counts[column] = counts.get(column, 0) + 1
    return sorted(column for column, count in counts.items() if count > 1)


def update_statements(statements: Iterable[str]) -> List[str]:
    """Only the ``UPDATE`` statements from ``statements``."""
    return [sql for sql in statements if sql.lstrip().upper().startswith("UPDATE")]


def capture_executed_sql(executor: Any, action: Callable[[], Any]) -> List[str]:
    """Run ``action`` and return every SQL string it executed.

    ``executor`` is the object whose ``execute`` the action goes through (the
    ``Database`` instance, normally). The original bound method is restored
    even if the action raises.
    """
    captured: List[str] = []
    original = executor.execute

    def _record(sql: Any, params: Any = None, *args: Any, **kwargs: Any) -> Any:
        captured.append(str(sql))
        return original(sql, params, *args, **kwargs)

    executor.execute = _record
    try:
        action()
    finally:
        executor.execute = original
    return captured


def assert_no_duplicate_assignments(statements: Iterable[str]) -> None:
    """Fail if any UPDATE in ``statements`` assigns a column twice."""
    for sql in update_statements(statements):
        duplicates = duplicate_set_columns(sql)
        assert duplicates == [], (
            "UPDATE assigns the same column more than once "
            f"({', '.join(duplicates)}). SQLite tolerates this by letting the "
            "last assignment win, but PostgreSQL rejects the statement with "
            '"multiple assignments to same column". Emit the column once '
            f"(every per-column statement already writes updated_at): {sql}"
        )
